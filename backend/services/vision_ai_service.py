"""
Vision AI Service - Generates "badass" AI insights for NBA player props
Uses Google Gemini via direct API

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

logger = logging.getLogger(__name__)

# Configuration - Use Google Gemini API Key
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_SERVICE_ROLE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')


class VisionAIService:
    """
    The AI "Oracle" - Generates sharp, aggressive sports betting insights
    using Claude Sonnet 4.5 via Emergent integration.
    
    KEY UPGRADE: Now includes "Conflict Finder" logic to detect anomalies
    and feed the AI with comparison data, not just raw stats.
    """
    
    # The PROPHETIC System Prompt - Forces AI to hunt for the story
    SYSTEM_PROMPT = """You are an aggressive betting sharp with access to 2026's most advanced analytics.
You are given a player's season averages AND their current game context (Pace, Fatigue, Injuries, Usage Shifts).

YOUR MISSION:
1. IDENTIFY THE ANOMALY. Is the pace 10%+ different? Is a high-usage teammate OUT? Is this a revenge game?
2. If data appears "standard," find a SECONDARY FACTOR: Home vs Away splits, contract year motivation, or defensive matchup weakness.
3. NEVER say "standard projection" or "no significant modifiers" - there is ALWAYS a story.

Write a 1-sentence VISION INSIGHT that explains why THIS SPECIFIC GAME is NOT a standard day.

TONE: High-stakes, punchy, prophetic. No fillers like "it appears" or "based on the data."
Speak as if you've already seen the future. Maximum 25 words.
Examples of good insights:
- "The Rockets' pace surge (+12%) turns Sengun into a stat-stuffing machine tonight—expect a ceiling game."
- "With VanVleet sidelined, Jalen Green inherits 28% usage vacuum; this line is 3 points too low."
- "Revenge narrative against his former team + fastest pace opponent = perfect storm for the Over."
"""

    # Conflict detection thresholds
    PACE_ANOMALY_THRESHOLD = 0.08  # 8% pace difference is significant
    USAGE_BUMP_THRESHOLD = 5  # 5%+ usage bump is significant
    HIT_RATE_ANOMALY = 15  # 15%+ difference between L5 and season is a trend shift
    BLOWOUT_SPREAD_THRESHOLD = 12  # -12 or more indicates blowout risk

    def __init__(self, db):
        """Initialize with MongoDB database connection."""
        self.db = db
        self.daily_insights = db.dg_daily_insights
        self.cached_board = db.dg_cached_board
        
        if not GEMINI_API_KEY:
            logger.warning("[VISION] GEMINI_API_KEY not found - AI insights will be disabled")
    
    def _detect_conflicts(
        self,
        pace_factor: float,
        usage_bump: float,
        l5_rate: float,
        l10_rate: float,
        season_avg: float,
        current_line: float,
        fatigue: str,
        is_back_to_back: bool = False,
        spread: float = 0,
        is_home: bool = True,
        injured_teammates: List[str] = None
    ) -> List[Dict[str, str]]:
        """
        The CONFLICT FINDER - Detects anomalies that make a game special.
        Returns a list of conflicts/stories to feed to the AI.
        """
        conflicts = []
        
        # 1. PACE SHIFT ANOMALY
        pace_diff = (pace_factor - 1.0) * 100
        if abs(pace_diff) >= self.PACE_ANOMALY_THRESHOLD * 100:
            direction = "faster" if pace_diff > 0 else "slower"
            conflicts.append({
                "type": "PACE_SHIFT",
                "severity": "HIGH" if abs(pace_diff) > 12 else "MEDIUM",
                "context": f"Tonight's matchup is {abs(pace_diff):.0f}% {direction} than league average pace. {'Run-and-gun game expected.' if pace_diff > 0 else 'Grind-it-out defensive battle.'}"
            })
        
        # 2. USAGE VACUUM (Injured Star)
        if usage_bump >= self.USAGE_BUMP_THRESHOLD:
            teammate_names = ", ".join(injured_teammates[:2]) if injured_teammates else "key teammates"
            conflicts.append({
                "type": "USAGE_VACUUM",
                "severity": "HIGH",
                "context": f"With {teammate_names} OUT, there's a {usage_bump:.0f}% usage vacuum. Extra shots and touches expected."
            })
        
        # 3. HOT/COLD STREAK (L5 vs Season divergence)
        if season_avg > 0:
            l5_projection = (l5_rate / 100) * current_line * 1.5  # Rough estimate
            season_projection = season_avg
            streak_diff = abs(l5_rate - (l10_rate if l10_rate > 0 else 50))
            
            if streak_diff >= self.HIT_RATE_ANOMALY:
                if l5_rate > l10_rate:
                    conflicts.append({
                        "type": "HOT_STREAK",
                        "severity": "HIGH",
                        "context": f"Player is SCORCHING HOT: L5 hit rate ({l5_rate:.0f}%) is {streak_diff:.0f}% above L10. Momentum is real."
                    })
                else:
                    conflicts.append({
                        "type": "COLD_STREAK",
                        "severity": "MEDIUM",
                        "context": f"Player is ICE COLD: L5 hit rate ({l5_rate:.0f}%) dropped {streak_diff:.0f}% from L10. Regression or slump?"
                    })
        
        # 4. BLOWOUT RISK
        if abs(spread) >= self.BLOWOUT_SPREAD_THRESHOLD:
            if spread < 0:  # Team is heavy favorite
                conflicts.append({
                    "type": "BLOWOUT_RISK",
                    "severity": "HIGH",
                    "context": f"Heavy {abs(spread):.1f}-point favorite. Blowout risk = early benching in 4th quarter. Under bias."
                })
            else:  # Team is heavy underdog
                conflicts.append({
                    "type": "GARBAGE_TIME",
                    "severity": "MEDIUM",
                    "context": f"Heavy {spread:.1f}-point underdog. Garbage time = extended minutes for bench. Check backups."
                })
        
        # 5. FATIGUE FACTOR (Back-to-Back)
        if is_back_to_back or fatigue == "Fatigued":
            conflicts.append({
                "type": "FATIGUE",
                "severity": "MEDIUM",
                "context": "Back-to-back game. Historical data shows 5-8% production drop on second night of B2Bs."
            })
        
        # 6. HOME/AWAY SPLIT
        if not is_home:
            conflicts.append({
                "type": "ROAD_GAME",
                "severity": "LOW",
                "context": "Road game. Most players see 3-5% stat reduction away from home crowd."
            })
        
        # 7. LINE VS SEASON AVERAGE GAP
        if season_avg > 0 and current_line > 0:
            line_gap = ((season_avg - current_line) / current_line) * 100
            if abs(line_gap) >= 10:
                if line_gap > 0:
                    conflicts.append({
                        "type": "VALUE_LINE",
                        "severity": "HIGH",
                        "context": f"Line is {abs(line_gap):.0f}% BELOW season average ({season_avg:.1f}). Books are disrespecting this player."
                    })
                else:
                    conflicts.append({
                        "type": "INFLATED_LINE",
                        "severity": "MEDIUM",
                        "context": f"Line is {abs(line_gap):.0f}% ABOVE season average ({season_avg:.1f}). Public money may be inflating this."
                    })
        
        return conflicts
    
    def _build_conflict_context(self, conflicts: List[Dict[str, str]]) -> str:
        """Build a context string from detected conflicts for the AI."""
        if not conflicts:
            # Even if no major conflicts, find something interesting
            return "\nCONTEXT: No major anomalies detected. Focus on matchup-specific factors, historical trends vs this opponent, or motivation narratives (contract year, revenge game, milestone chase)."
        
        context_parts = ["\n=== ANOMALIES DETECTED ==="]
        
        # Sort by severity
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        sorted_conflicts = sorted(conflicts, key=lambda x: severity_order.get(x.get("severity", "LOW"), 2))
        
        for conflict in sorted_conflicts:
            severity_emoji = "🔥" if conflict["severity"] == "HIGH" else "⚡" if conflict["severity"] == "MEDIUM" else "📊"
            context_parts.append(f"{severity_emoji} [{conflict['type']}]: {conflict['context']}")
        
        context_parts.append("\nYour insight MUST reference at least one of these anomalies.")
        
        return "\n".join(context_parts)
        
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
        projected_score: Optional[float] = None,
        team: str = "",
        team_injury_summary: str = "",
        l5_rate: float = 0,
        season_avg: float = 0,
        spread: float = 0,
        is_home: bool = True,
        is_back_to_back: bool = False,
        injured_teammates: List[str] = None,
        # DvP parameters
        opponent_team: str = "",
        dvp_rank: int = 15,
        position: str = ""
    ) -> Dict[str, Any]:
        """
        Generate a single AI insight for a player prop.
        Now includes CONFLICT FINDER logic to detect anomalies.
        Now includes mandatory DvP defensive ranking context.
        Uses Google Gemini API.
        """
        if not GEMINI_API_KEY:
            return {
                "success": False,
                "error": "GEMINI_API_KEY not configured",
                "insight": None
            }
        
        try:
            # Determine player classification
            player_type = "DEMON (High Payout)" if is_demon else "GOBLIN (High Safety)" if is_goblin else "Standard"
            
            # Build DvP friction context sentence (MANDATORY)
            friction_level = "Low" if dvp_rank >= 25 else "High" if dvp_rank <= 5 else "Medium"
            dvp_context_sentence = f"The {opponent_team or 'opponent'} are currently ranked #{dvp_rank} against {position or 'this position'} in {stat_type.upper()}, creating a {friction_level} friction environment for this line."
            
            # RUN THE CONFLICT FINDER
            conflicts = self._detect_conflicts(
                pace_factor=pace_factor,
                usage_bump=usage_bump,
                l5_rate=l5_rate if l5_rate > 0 else l10_rate,
                l10_rate=l10_rate,
                season_avg=season_avg,
                current_line=current_line,
                fatigue=fatigue,
                is_back_to_back=is_back_to_back,
                spread=spread,
                is_home=is_home,
                injured_teammates=injured_teammates or []
            )
            
            # Build conflict context for AI
            conflict_context = self._build_conflict_context(conflicts)
            
            # Determine if this is a "special" insight (has HIGH severity conflicts)
            has_high_conflict = any(c.get("severity") == "HIGH" for c in conflicts)
            
            # Check for discrepancy edge (>15% difference)
            discrepancy_note = ""
            if projected_score and current_line > 0:
                discrepancy_pct = abs((projected_score - current_line) / current_line) * 100
                if discrepancy_pct > 15:
                    direction = "OVER" if projected_score > current_line else "UNDER"
                    discrepancy_note = f"\n🎯 EDGE ALERT: Model projects {projected_score:.1f} vs line {current_line}. {discrepancy_pct:.0f}% discrepancy favors {direction}."
            
            # Build injury context note
            injury_note = ""
            if team_injury_summary:
                injury_note = f"\n🏥 INJURY INTEL ({team}): {team_injury_summary}"
            
            # Build the enhanced prompt
            user_prompt = f"""
=== PLAYER PROFILE ===
PLAYER: {player_name}
TEAM: {team}
PROP TYPE: {stat_type.upper()}
CLASSIFICATION: {player_type}
CURRENT LINE: {current_line}
POSITION: {position or 'N/A'}

=== PERFORMANCE DATA ===
Season Average: {f'{season_avg:.1f}' if season_avg > 0 else 'N/A'}
L10 Hit Rate: {l10_rate}%
L5 Hit Rate: {l5_rate if l5_rate > 0 else l10_rate}%

=== DEFENSIVE MATCHUP (DvP) ===
{dvp_context_sentence}

=== GAME CONTEXT ===
Opponent: {opponent_team or 'TBD'}
Pace Factor: {'+' if pace_factor > 1 else ''}{((pace_factor - 1) * 100):.0f}% vs league avg
Fatigue: {fatigue} {'(BACK-TO-BACK)' if is_back_to_back else ''}
Usage Bump: {'+' if usage_bump > 0 else ''}{usage_bump:.0f}%
Location: {'HOME' if is_home else 'AWAY'}
Spread: {spread if spread else 'N/A'}
Volatility: {volatility}
{discrepancy_note}
{injury_note}
{conflict_context}

Generate a 1-sentence VISION INSIGHT. You MUST include the DvP matchup context (friction level) in your insight. Do NOT say "standard projection." Find the story."""

            # Initialize Google Gemini
            from google import genai
            
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            # Build full prompt with system context
            full_prompt = f"{self.SYSTEM_PROMPT}\n\n{user_prompt}"
            
            # Call Gemini API
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.1-flash-lite-preview",
                contents=full_prompt
            )
            
            insight = response.text.strip().strip('"').strip("'")
            
            logger.info(f"[VISION] Generated insight for {player_name}: {insight[:50]}...")
            
            return {
                "success": True,
                "player": player_name,
                "insight": insight,
                "classification": player_type,
                "has_high_conflict": has_high_conflict,
                "conflicts_count": len(conflicts),
                "conflict_types": [c["type"] for c in conflicts],
                "dvp_context": dvp_context_sentence,
                "dvp_rank": dvp_rank,
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
        if not GEMINI_API_KEY:
            return {
                "success": False,
                "error": "GEMINI_API_KEY not configured",
                "insights_generated": 0
            }
        
        # Filter for eligible players (Demons, Goblins, High Volatility)
        eligible_players = [
            p for p in players 
            if p.get('is_demon') or p.get('is_goblin') or p.get('volatility_score') == 'High'
        ]
        
        logger.info(f"[VISION] Processing {len(eligible_players)} eligible players out of {len(players)} total")
        
        # Pre-fetch injury summaries for all teams
        team_injury_cache = {}
        unique_teams = set(p.get('team', '') for p in eligible_players if p.get('team'))
        
        from services.injury_service import get_injury_service
        injury_service = get_injury_service(self.db)
        
        for team in unique_teams:
            try:
                team_injury_cache[team] = await injury_service.get_team_injury_summary(team)
            except Exception:
                team_injury_cache[team] = ""
        
        results = []
        errors = []
        
        # Process in batches to respect rate limits
        for i in range(0, len(eligible_players), max_concurrent):
            batch = eligible_players[i:i + max_concurrent]
            
            tasks = []
            for player in batch:
                team = player.get('team', '')
                task = self.generate_single_insight(
                    player_name=player.get('player_name', 'Unknown'),
                    stat_type=player.get('stat_type', 'points'),
                    current_line=player.get('line', 0),
                    l10_rate=player.get('l10_hit_rate', 50),
                    l5_rate=player.get('l5_hit_rate', 0),
                    season_avg=player.get('season_avg', 0),
                    pace_factor=player.get('pace_adjustment_factor', 1.0),
                    fatigue="Fatigued" if player.get('is_back_to_back') else "Normal",
                    usage_bump=player.get('usage_bump_percent', 0),
                    volatility=player.get('volatility_score', 'Med'),
                    is_demon=player.get('is_demon', False),
                    is_goblin=player.get('is_goblin', False),
                    team=team,
                    team_injury_summary=team_injury_cache.get(team, ''),
                    is_back_to_back=player.get('is_back_to_back', False),
                    is_home=player.get('is_home', True),
                    spread=player.get('spread', 0),
                    injured_teammates=player.get('injured_teammates', [])
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
                        "ai_model": "gemini-3.1-flash-lite-preview",
                        "classification": result.get('classification', 'Standard'),
                        "has_high_conflict": result.get('has_high_conflict', False),
                        "conflict_types": result.get('conflict_types', [])
                    }
                },
                upsert=False  # Only update existing records
            )
        except Exception as e:
            logger.error(f"[VISION] Failed to save insight to DB: {e}")
    
    async def trigger_insights_for_sync(self) -> Dict[str, Any]:
        """
        Trigger AI insight generation for all eligible players in the current sync.
        Now also pulls from radar_picks and goblin_vault for more coverage.
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
        
        # Also get radar picks and vault picks for priority processing
        radar_cursor = self.db.dg_radar_picks.find({}, {"_id": 0, "player_name": 1})
        radar_players = {p['player_name'] for p in await radar_cursor.to_list(100)}
        
        vault_cursor = self.db.dg_goblin_vault.find({}, {"_id": 0, "player_name": 1})
        vault_players = {p['player_name'] for p in await vault_cursor.to_list(100)}
        
        logger.info(f"[VISION] Radar players: {len(radar_players)}, Vault players: {len(vault_players)}")
        
        # Enrich with demon/goblin status from cached_board and radar/vault membership
        enriched_players = []
        for player in players:
            player_name = player.get('player_name')
            
            # Mark as demon if in radar picks
            if player_name in radar_players:
                player['is_demon'] = True
            
            # Mark as goblin if in vault picks
            if player_name in vault_players:
                player['is_goblin'] = True
            
            # Also check cached board for additional demon/goblin props
            cached = await self.cached_board.find_one(
                {"player_name": player_name},
                {"_id": 0, "props": 1, "team_abbreviation": 1}
            )
            
            if cached:
                player['team'] = cached.get('team_abbreviation', player.get('team', ''))
                
                if cached.get('props'):
                    # Check if player has any demon or goblin props
                    has_demon = any(
                        p.get('value_score', 0) > 1.3 
                        for p in cached['props'] if isinstance(p, dict)
                    )
                    has_goblin = any(
                        p.get('hit_rate_l10', 0) >= 70 
                        for p in cached['props'] if isinstance(p, dict)
                    )
                    
                    if has_demon:
                        player['is_demon'] = True
                    if has_goblin:
                        player['is_goblin'] = True
            
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
