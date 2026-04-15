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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


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
        self._api_available = bool(GEMINI_API_KEY)
        
        if not self._api_available:
            logger.warning("[VISION] No GEMINI_API_KEY - Strategic Vision disabled")
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
        
        # Normalize prop_type - handle short codes and full names
        prop_type_lower = prop_type.lower().strip()
        
        # Map short codes to full stat names
        stat_code_map = {
            "pts": "points",
            "reb": "rebounds", 
            "ast": "assists",
            "stl": "steals",
            "blk": "blocks",
            "to": "turnovers",
            "3pm": "threes",
            "3pt": "threes",
            "pra": "points+rebounds+assists",
            "p+r+a": "points+rebounds+assists",
            "pr": "points+rebounds",
            "p+r": "points+rebounds", 
            "pa": "points+assists",
            "p+a": "points+assists",
            "ra": "rebounds+assists",
            "r+a": "rebounds+assists"
        }
        
        # Normalize the prop type
        normalized_prop = stat_code_map.get(prop_type_lower, prop_type_lower)
        
        # Also check for keywords in longer strings
        if "point" in normalized_prop and "rebound" not in normalized_prop and "assist" not in normalized_prop:
            normalized_prop = "points"
        elif "rebound" in normalized_prop and "point" not in normalized_prop and "assist" not in normalized_prop:
            normalized_prop = "rebounds"
        elif "assist" in normalized_prop and "point" not in normalized_prop and "rebound" not in normalized_prop:
            normalized_prop = "assists"
        
        # Extract stats
        l10_avg = l10_stats.get("avg", 0)
        l10_hit_rate = l10_stats.get("hit_rate_pct", 0)
        l10_over = l10_stats.get("games_over", 0)
        l10_games = l10_stats.get("total_games", 10)
        
        # Determine STRICT stat-specific context based on normalized prop
        if normalized_prop == "rebounds" or "rebound" in normalized_prop:
            stat_focus = "REBOUNDS"
            matchup_focus = "rebounding, glass work, and board crashing"
            opponent_weakness = "weak rebounding, poor box-outs, or undersized frontcourt"
            forbidden = "DO NOT mention scoring, assists, passing, or playmaking"
            example = f'"{opponent} ranks bottom-10 in defensive rebounding rate and their undersized frontcourt struggles to box out aggressive glass-crashers. {player_name} averages {l10_avg:.1f} boards over his last 10, making this {target_line} line easy money against their weak interior presence."'
            
        elif normalized_prop == "assists" or "assist" in normalized_prop:
            stat_focus = "ASSISTS"
            matchup_focus = "playmaking, passing, and creating for teammates"
            opponent_weakness = "porous help defense, poor rotations, or giving up open shooters"
            forbidden = "DO NOT mention scoring, rebounds, or defensive stats"
            example = f'"{opponent}\'s help defense collapses too aggressively, leaving shooters wide open on kick-outs for {player_name} to find. His {l10_avg:.1f} assist average makes this {target_line} line exploitable against their broken defensive rotations."'
            
        elif normalized_prop == "points" or "point" in normalized_prop:
            stat_focus = "POINTS"
            matchup_focus = "scoring, shot creation, and offensive output"
            opponent_weakness = "weak perimeter defense, poor rim protection, or can't guard isolation"
            forbidden = "DO NOT mention rebounds, assists, or defensive stats"
            example = f'"{opponent}\'s perimeter defense is bottom-5 in the league, giving elite scorers clean looks all game. {player_name}\'s {l10_avg:.1f} PPG makes this {target_line} line a gift against their porous coverage."'
            
        elif normalized_prop == "blocks" or "block" in normalized_prop:
            stat_focus = "BLOCKS"
            matchup_focus = "rim protection, shot blocking, and interior defense"
            opponent_weakness = "heavy reliance on rim attacks, weak floater game, or poor finishing"
            forbidden = "DO NOT mention scoring, assists, or perimeter defense"
            example = f'"{opponent} attacks the rim on 45% of possessions, playing right into {player_name}\'s elite shot-blocking. His {l10_avg:.1f} blocks per game makes this {target_line} line exploitable."'
            
        elif normalized_prop == "steals" or "steal" in normalized_prop:
            stat_focus = "STEALS"
            matchup_focus = "ball-hawking, passing lane disruption, and active hands"
            opponent_weakness = "turnover-prone guards, careless passing, or poor ball security"
            forbidden = "DO NOT mention scoring, rebounds, or offensive production"
            example = f'"{opponent}\'s guards average 4+ turnovers per game with predictable passing patterns. {player_name}\'s {l10_avg:.1f} steals average makes this {target_line} line easy against their careless ball-handling."'
            
        elif normalized_prop == "turnovers" or "turnover" in normalized_prop:
            stat_focus = "TURNOVERS"
            matchup_focus = "ball security and turnover tendencies"
            opponent_weakness = "aggressive trapping, active hands, or forcing turnovers"
            forbidden = "DO NOT mention scoring, rebounds, or assists"
            example = f'"{opponent}\'s trapping defense forces high turnover rates from opposing ball-handlers. {player_name}\'s {l10_avg:.1f} turnover average makes this {target_line} line accurately set."'
            
        elif normalized_prop == "threes" or "3" in normalized_prop or "three" in normalized_prop:
            stat_focus = "THREE-POINTERS"
            matchup_focus = "three-point shooting and perimeter offense"
            opponent_weakness = "poor closeouts, weak perimeter defense, or leaving shooters open"
            forbidden = "DO NOT mention rebounds, assists, or interior scoring"
            example = f'"{opponent} allows the most threes in the league with lazy closeouts. {player_name}\'s {l10_avg:.1f} threes per game makes this {target_line} line easy against their broken coverage."'
            
        elif "+" in normalized_prop or "combo" in normalized_prop:
            # Handle combo stats
            stat_focus = normalized_prop.upper().replace("+", " + ")
            matchup_focus = "combined production across multiple categories"
            opponent_weakness = "overall defensive weaknesses"
            forbidden = "Focus on the combined stat total"
            example = f'"{opponent}\'s defensive issues allow {player_name} to produce across multiple categories. His {l10_avg:.1f} combined average makes this {target_line} line exploitable."'
            
        else:
            stat_focus = prop_type.upper()
            matchup_focus = f"{prop_type} production"
            opponent_weakness = "matchup vulnerabilities"
            forbidden = "Focus ONLY on this specific stat"
            example = f'"Based on matchup analysis, {player_name} should exceed {target_line} {prop_type}."'
        
        # Calculate edge
        edge_vs_line = ((l10_avg - target_line) / target_line * 100) if target_line > 0 else 0
        
        # Build the STRICT prompt
        prompt = f"""Write EXACTLY 2 sentences about this NBA bet. {forbidden}.

THE BET: {player_name} ({team}) {direction} {target_line} {stat_focus} vs {opponent}

RULES:
1. This is a {stat_focus} bet - ONLY discuss {matchup_focus}
2. Sentence 1: Why {opponent}'s {opponent_weakness} helps {player_name} get {stat_focus}
3. Sentence 2: Why the {target_line} line is mispriced (include a number)
4. {forbidden}

PLAYER {stat_focus} STATS: L10 avg {l10_avg:.1f}, hit rate {l10_hit_rate:.0f}%

EXAMPLE: {example}

YOUR 2-SENTENCE OUTPUT:"""

        return prompt
    
    async def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini Flash API for text generation."""
        
        try:
            from google import genai
            
            client = genai.Client(api_key=GEMINI_API_KEY)
            
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
            return {"success": False, "error": "GEMINI_API_KEY not configured"}
        
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
            
            # IMPORTANT: Always use the pick's stat_type, not the prop's market
            # The pick's stat_type is the specific bet we're analyzing
            stat_type = pick.get("stat_type", demon_prop.get("market", "points"))
            
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
            
            # Generate thesis using the PICK's stat_type
            thesis = await self.generate_strategic_thesis(
                player_name=player_name,
                prop_type=stat_type,  # Use pick's stat_type directly
                target_line=pick.get("demon_line", demon_prop.get("line", 0)),
                direction=pick.get("direction", "Over"),
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
        
        # 3. Process Parlay Builder Picks (all players in parlays)
        parlay_collections = [
            self.db.dg_parlays_demon,
            self.db.dg_parlays_goblin
        ]
        
        for parlay_col in parlay_collections:
            try:
                parlays = await parlay_col.find({}, {"_id": 0}).to_list(length=50)
                logger.info(f"[VISION] Processing parlays from {parlay_col.name}")
                
                for parlay in parlays:
                    picks = parlay.get("picks", [])
                    for pick in picks:
                        player_name = pick.get("player_name")
                        if not player_name or player_name in processed_players:
                            continue
                        
                        player_data = await self.cached_board.find_one(
                            {"player_name": player_name},
                            {"_id": 0}
                        )
                        
                        if not player_data:
                            continue
                        
                        # Get prop info from pick or player_data
                        stat_type = pick.get("stat_type", "points")
                        line = pick.get("line", pick.get("demon_line", pick.get("goblin_line", 0)))
                        
                        # Find matching prop in player_data
                        props = player_data.get("props", [])
                        matching_prop = None
                        for p in props:
                            if stat_type.lower() in p.get("market", "").lower():
                                matching_prop = p
                                break
                        
                        if not matching_prop and props:
                            matching_prop = props[0]
                        
                        if not matching_prop:
                            matching_prop = {}
                        
                        hit_rates = matching_prop.get("hit_rates", {})
                        l10_stats = hit_rates.get("l10", {})
                        l5_stats = hit_rates.get("l5", {})
                        
                        if not l10_stats:
                            l10_stats = {
                                "avg": pick.get("season_avg", 0),
                                "hit_rate_pct": pick.get("h10_rate", 0) * 100 if pick.get("h10_rate", 0) < 1 else pick.get("h10_rate", 0),
                                "games_over": pick.get("h10_over", 0),
                                "total_games": pick.get("h10_games", 10)
                            }
                        
                        is_demon = pick.get("is_demon", False)
                        is_goblin = pick.get("is_goblin", False)
                        
                        thesis = await self.generate_strategic_thesis(
                            player_name=player_name,
                            prop_type=stat_type,
                            target_line=line,
                            direction=pick.get("direction", "Over"),
                            team=player_data.get("team", pick.get("team", "")),
                            opponent=matching_prop.get("away_team", "") or matching_prop.get("home_team", ""),
                            l10_stats=l10_stats,
                            l5_stats=l5_stats,
                            position=player_data.get("position", ""),
                            injury_context=player_data.get("injury_context", ""),
                            game_id=matching_prop.get("event_id", ""),
                            is_demon=is_demon,
                            is_goblin=is_goblin
                        )
                        
                        if thesis:
                            await self.cached_board.update_one(
                                {"player_name": player_name},
                                {"$set": {"intel_briefing": thesis, "has_vision": True}}
                            )
                            generated += 1
                            processed_players.add(player_name)
                        else:
                            errors += 1
                        
                        await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"[VISION] Error processing parlay collection: {e}")
        
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
