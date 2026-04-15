"""
AI Context Engine
==================

Standalone service that evaluates player context from news/injury reports
and updates the nba_master_hub_2026 with AI-derived context scores.

Context Score Scale:
- 0.0 = Massive red flag (injury, suspension, major negative)
- 0.5 = Neutral (no significant news impact)
- 1.0 = Massive positive boost (revenge game, teammates out, hot streak)
"""

import asyncio
import json
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_context_engine")

# Environment variables
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME", "pick_vision")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")


class AiContextEngine:
    """
    AI Context Engine - Evaluates player news and updates context scores.
    
    This engine:
    1. Fetches recent news/injury reports for players
    2. Sends to LLM for impact evaluation
    3. Updates nba_master_hub_2026 with ai_context_score and ai_context_reason
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.nba_master_hub_2026
        self.news_cache = db.ai_news_cache
        self._api_available = bool(GOOGLE_API_KEY)
        
        if not self._api_available:
            logger.warning("[AI_CONTEXT] GOOGLE_API_KEY not set - engine will use neutral defaults")
    
    # ==================== DATA GATHERING PHASE ====================
    
    async def fetch_player_news(self, player_name: str) -> List[Dict[str, Any]]:
        """
        Fetch latest news blurbs or injury reports for a specific player.
        
        For now, this uses mock data and checks for known injury patterns.
        In production, this would integrate with:
        - ESPN API
        - RotoBaller
        - Rotoworld
        - Twitter/X feeds
        """
        news_items = []
        
        # Check for cached news first
        cached = await self.news_cache.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        
        if cached and cached.get("news_items"):
            return cached.get("news_items", [])
        
        # Mock news generation based on player context
        # In production, replace with actual news API calls
        player_data = await self.master_hub.find_one(
            {"$or": [{"display_name": player_name}, {"player_name": player_name}]},
            {"_id": 0, "team": 1, "position": 1, "injury": 1, "recent_performance": 1}
        )
        
        if player_data:
            # Check for injury status
            injury = player_data.get("injury")
            if injury and isinstance(injury, dict):
                status = injury.get("status", "")
                if status and status.lower() not in ["healthy", "active", ""]:
                    news_items.append({
                        "source": "injury_report",
                        "headline": f"{player_name} listed as {status}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "sentiment": "negative" if "out" in status.lower() else "cautionary"
                    })
            elif injury and isinstance(injury, str) and injury.lower() not in ["healthy", "active", ""]:
                news_items.append({
                    "source": "injury_report",
                    "headline": f"{player_name} injury status: {injury}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sentiment": "cautionary"
                })
            
            # Check for recent performance trends (mock)
            recent_perf = player_data.get("recent_performance", {})
            if recent_perf.get("hot_streak"):
                news_items.append({
                    "source": "performance_tracker",
                    "headline": f"{player_name} on a hot streak - exceeded props in last 5 games",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sentiment": "positive"
                })
        
        # If no specific news, return status with player name context
        if not news_items:
            news_items.append({
                "source": "status_check",
                "headline": f"{player_name} - active and available. No injury or lineup changes reported.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sentiment": "neutral"
            })
        
        return news_items
    
    # ==================== LLM EVALUATION PHASE ====================
    
    async def evaluate_player_context(self, player_name: str, recent_news: List[Dict]) -> Dict[str, Any]:
        """
        Send player news to LLM for impact evaluation.
        
        Returns:
            {
                "context_shift": float (0.0 to 1.0),
                "reason": str (1-sentence explanation)
            }
        """
        # Default neutral response - data-driven, not generic
        default_response = {
            "context_shift": 0.5,
            "reason": f"Baseline projection for {player_name}. No news-driven modifiers."
        }
        
        if not self._api_available:
            return default_response
        
        if not recent_news:
            return default_response
        
        # Format news for LLM
        news_text = "\n".join([
            f"- [{item.get('source', 'unknown')}] {item.get('headline', '')}"
            for item in recent_news
        ])
        
        # Build the evaluation prompt
        system_prompt = f"""You are an elite NBA sports betting quantitative analyst. Read the following recent news for {player_name}. Assess how this news impacts their likelihood of hitting their over/under player props tonight (e.g., injuries to teammates increasing their usage, minutes restrictions, revenge games). Output ONLY a valid JSON object with two keys: 'context_shift' (a float between 0.0 and 1.0, where 0.5 is neutral, 0.0 is a massive red flag/injury, and 1.0 is a massive positive boost) and 'reason' (a concise 1-sentence explanation).

Recent news for {player_name}:
{news_text}

Output ONLY valid JSON, no markdown, no explanation:"""

        try:
            result = await self._call_gemini(system_prompt)
            
            if result:
                # Parse JSON response
                parsed = json.loads(result)
                
                # Validate response structure
                context_shift = parsed.get("context_shift", 0.5)
                reason = parsed.get("reason", "No reason provided.")
                
                # Clamp context_shift to valid range
                context_shift = max(0.0, min(1.0, float(context_shift)))
                
                return {
                    "context_shift": context_shift,
                    "reason": str(reason)[:500]  # Limit reason length
                }
        
        except json.JSONDecodeError as e:
            logger.warning(f"[AI_CONTEXT] Invalid JSON from LLM for {player_name}: {e}")
        except Exception as e:
            logger.error(f"[AI_CONTEXT] Error evaluating {player_name}: {e}")
        
        return default_response
    
    async def _call_gemini(self, prompt: str) -> Optional[str]:
        """Call Gemini Flash API for context evaluation."""
        
        try:
            from google import genai
            
            client = genai.Client(api_key=GOOGLE_API_KEY)
            
            def generate():
                return client.models.generate_content(
                    model="gemini-3-flash-preview",
                    contents=prompt
                )
            
            response = await asyncio.get_event_loop().run_in_executor(None, generate)
            
            if response and response.text:
                # Clean up response - extract JSON
                text = response.text.strip()
                
                # Remove markdown code blocks if present
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                
                return text.strip()
            
        except ImportError:
            logger.error("[AI_CONTEXT] google-genai package not installed")
        except Exception as e:
            logger.error(f"[AI_CONTEXT] Gemini API error: {e}")
        
        return None
    
    # ==================== DATABASE INJECTION PHASE ====================
    
    async def update_master_hub_with_context(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """
        Loop through all active players in nba_master_hub_2026 and update
        each with ai_context_score and ai_context_reason.
        
        Args:
            limit: Optional limit on number of players to process (for testing)
        
        Returns:
            Summary dict with counts and any errors
        """
        logger.info("[AI_CONTEXT] Starting context score update for nba_master_hub_2026...")
        
        start_time = datetime.now(timezone.utc)
        
        # Get all active players
        query = {"is_active": {"$ne": False}}
        cursor = self.master_hub.find(query, {"_id": 0, "display_name": 1, "player_name": 1, "player_id": 1})
        
        if limit:
            cursor = cursor.limit(limit)
        
        players = await cursor.to_list(None)
        total_players = len(players)
        
        logger.info(f"[AI_CONTEXT] Found {total_players} players to evaluate")
        
        updated = 0
        errors = 0
        skipped = 0
        
        for i, player in enumerate(players):
            # Support both display_name and player_name
            player_name = player.get("display_name") or player.get("player_name")
            player_id = player.get("player_id")
            
            if not player_name:
                skipped += 1
                continue
            
            try:
                # Fetch news for player
                news = await self.fetch_player_news(player_name)
                
                # Evaluate context with LLM
                evaluation = await self.evaluate_player_context(player_name, news)
                
                # Update master hub with context score (use display_name as key)
                await self.master_hub.update_one(
                    {"$or": [{"display_name": player_name}, {"player_name": player_name}]},
                    {
                        "$set": {
                            "ai_context_score": evaluation["context_shift"],
                            "ai_context_reason": evaluation["reason"],
                            "ai_context_updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
                
                updated += 1
                
                # Log progress every 50 players
                if (i + 1) % 50 == 0:
                    logger.info(f"[AI_CONTEXT] Progress: {i + 1}/{total_players} players processed")
                
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"[AI_CONTEXT] Error processing {player_name}: {e}")
                errors += 1
                
                # Set neutral defaults on error
                try:
                    await self.master_hub.update_one(
                        {"player_name": player_name},
                        {
                            "$set": {
                                "ai_context_score": 0.5,
                                "ai_context_reason": "Evaluation failed - using neutral default",
                                "ai_context_updated_at": datetime.now(timezone.utc).isoformat()
                            }
                        }
                    )
                except:
                    pass
        
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        # Summary
        summary = {
            "success": True,
            "total_players": total_players,
            "updated": updated,
            "errors": errors,
            "skipped": skipped,
            "duration_seconds": round(duration, 2),
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat()
        }
        
        logger.info(f"[AI_CONTEXT] === CONTEXT ENGINE COMPLETE ===")
        logger.info(f"[AI_CONTEXT] Updated {updated} players with AI Context Scores")
        logger.info(f"[AI_CONTEXT] Errors: {errors}, Skipped: {skipped}")
        logger.info(f"[AI_CONTEXT] Duration: {duration:.1f} seconds")
        
        return summary
    
    # ==================== SINGLE PLAYER EVALUATION ====================
    
    async def evaluate_single_player(self, player_name: str) -> Dict[str, Any]:
        """
        Evaluate and update context for a single player.
        Useful for on-demand updates.
        """
        try:
            # Fetch news
            news = await self.fetch_player_news(player_name)
            
            # Evaluate
            evaluation = await self.evaluate_player_context(player_name, news)
            
            # Update database
            result = await self.master_hub.update_one(
                {"$or": [{"display_name": player_name}, {"player_name": player_name}]},
                {
                    "$set": {
                        "ai_context_score": evaluation["context_shift"],
                        "ai_context_reason": evaluation["reason"],
                        "ai_context_updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
            
            return {
                "success": True,
                "player_name": player_name,
                "context_score": evaluation["context_shift"],
                "reason": evaluation["reason"],
                "updated": result.modified_count > 0
            }
            
        except Exception as e:
            logger.error(f"[AI_CONTEXT] Error evaluating {player_name}: {e}")
            return {
                "success": False,
                "player_name": player_name,
                "error": str(e)
            }


# ==================== STANDALONE EXECUTION ====================

async def run_context_engine(limit: Optional[int] = None):
    """
    Standalone execution function.
    Can be run as: python ai_context_engine.py
    """
    if not MONGO_URL:
        logger.error("[AI_CONTEXT] MONGO_URL not set!")
        return
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    
    engine = AiContextEngine(db)
    result = await engine.update_master_hub_with_context(limit=limit)
    
    print("\n" + "="*50)
    print("AI CONTEXT ENGINE - EXECUTION SUMMARY")
    print("="*50)
    print(f"Total Players: {result.get('total_players', 0)}")
    print(f"Updated: {result.get('updated', 0)}")
    print(f"Errors: {result.get('errors', 0)}")
    print(f"Duration: {result.get('duration_seconds', 0)} seconds")
    print("="*50 + "\n")
    
    return result


if __name__ == "__main__":
    import sys
    
    # Optional limit argument for testing
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    
    asyncio.run(run_context_engine(limit=limit))
