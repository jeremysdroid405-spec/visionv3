"""
SOCIAL SIGNAL ENGINE - News Sentiment & Revenge Game Detection
==============================================================
Uses Tank01 API to detect:
1. Volatility flags (injuries, DNP, trades, suspensions, etc.)
2. Revenge games (player vs former team)

Data Sources:
- getNBAPlayerNews: News articles for volatility detection
- getNBAPlayerInfo: Previous teams for revenge game detection
- getNBATeamSchedule: Current opponent matching

Author: PickVision AI v3.2
"""

import os
import httpx
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Tank01 API Configuration
TANK01_API_KEY = os.environ.get("TANK01_API_KEY", "402edbcac6mshd04997e7ca01d17p1879eajsn65ab176cdb1e")
TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"
TANK01_BASE = "https://tank01-fantasy-stats.p.rapidapi.com"

# Keyword Lists for Volatility Detection
REDUCED_USAGE_KEYWORDS = [
    "out", "rest", "dnp", "inactive", "sidelined", "minutes restriction",
    "load management", "injury", "questionable", "doubtful", "ruled out"
]

VOLATILITY_KEYWORDS = [
    "traded", "waived", "personal reasons", "divorce", "suspended", 
    "internal matter", "disciplinary", "fine", "benched", "demoted",
    "trade request", "unhappy", "frustrated"
]

# Keywords that indicate rumor/speculation (should be filtered out)
RUMOR_KEYWORDS = [
    "rumor", "speculation", "reportedly", "sources say", "allegedly",
    "could be", "might be", "may be", "unconfirmed"
]


class SocialSignalEngine:
    """
    Engine for detecting social signals that affect betting confidence.
    
    Features:
    1. News Sentiment Analysis - Detects volatility from recent news
    2. Revenge Game Detection - Identifies players facing former teams
    """
    
    def __init__(self, db):
        self.db = db
        self.social_signals = db.dg_social_signals
        self.player_news_cache = db.dg_player_news_cache
        self.headers = {
            "x-rapidapi-key": TANK01_API_KEY,
            "x-rapidapi-host": TANK01_HOST
        }
        self.last_sync = None
    
    async def sync_social_signals(self, player_names: List[str] = None) -> Dict[str, Any]:
        """
        Main sync function - fetches news and detects revenge games.
        Should be called every 30 minutes.
        
        Args:
            player_names: Optional list of players to check. If None, checks all cached players.
        
        Returns:
            Sync results with counts of volatility and revenge flags detected.
        """
        sync_start = datetime.now(timezone.utc)
        logger.info("[SOCIAL SIGNAL] Starting social signal sync...")
        
        results = {
            "success": True,
            "synced_at": sync_start.isoformat(),
            "players_checked": 0,
            "volatility_flags": 0,
            "revenge_games": 0,
            "news_articles_scanned": 0,
            "signals": []
        }
        
        try:
            # Get player list from cached board if not provided
            if player_names is None:
                players = await self.db.dg_cached_board.distinct("player_name")
                player_names = list(players) if players else []
            
            if not player_names:
                logger.warning("[SOCIAL SIGNAL] No players to check")
                return results
            
            results["players_checked"] = len(player_names)
            
            # Process players in batches
            batch_size = 10
            all_signals = []
            
            for i in range(0, len(player_names), batch_size):
                batch = player_names[i:i+batch_size]
                
                for player_name in batch:
                    try:
                        signal = await self._process_player_signals(player_name)
                        
                        if signal:
                            all_signals.append(signal)
                            
                            if signal.get("volatility_flag"):
                                results["volatility_flags"] += 1
                            if signal.get("revenge_game"):
                                results["revenge_games"] += 1
                            
                            results["news_articles_scanned"] += signal.get("news_scanned", 0)
                        
                        await asyncio.sleep(0.2)  # Rate limiting
                        
                    except Exception as e:
                        logger.debug(f"[SOCIAL SIGNAL] Error processing {player_name}: {e}")
                
                # Progress log
                logger.info(f"[SOCIAL SIGNAL] Processed {min(i + batch_size, len(player_names))}/{len(player_names)} players")
            
            # Store signals in MongoDB
            if all_signals:
                await self.social_signals.delete_many({})
                await self.social_signals.insert_many(all_signals)
            
            results["signals"] = all_signals
            self.last_sync = sync_start
            
            logger.info(f"[SOCIAL SIGNAL] Sync complete: {results['volatility_flags']} volatility flags, {results['revenge_games']} revenge games")
            
        except Exception as e:
            logger.error(f"[SOCIAL SIGNAL] Sync error: {e}")
            results["success"] = False
            results["error"] = str(e)
        
        return results
    
    async def _process_player_signals(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Process a single player for volatility and revenge signals.
        
        Returns:
            Signal document with volatility_flag, revenge_game, and details.
        """
        signal = {
            "player_name": player_name,
            "volatility_flag": False,
            "volatility_reason": None,
            "volatility_source": None,
            "revenge_game": False,
            "revenge_opponent": None,
            "previous_team": None,
            "news_scanned": 0,
            "checked_at": datetime.now(timezone.utc).isoformat()
        }
        
        # 1. Check for volatility via news
        news_result = await self._check_player_news(player_name)
        if news_result:
            signal["volatility_flag"] = news_result.get("has_volatility", False)
            signal["volatility_reason"] = news_result.get("reason")
            signal["volatility_source"] = news_result.get("source")
            signal["volatility_headline"] = news_result.get("headline")
            signal["news_scanned"] = news_result.get("articles_scanned", 0)
        
        # 2. Check for revenge game
        revenge_result = await self._check_revenge_game(player_name)
        if revenge_result:
            signal["revenge_game"] = revenge_result.get("is_revenge", False)
            signal["revenge_opponent"] = revenge_result.get("opponent")
            signal["previous_team"] = revenge_result.get("previous_team")
        
        # Only return if there's a signal
        if signal["volatility_flag"] or signal["revenge_game"]:
            return signal
        
        return None
    
    async def _check_player_news(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Check Tank01 getNBAPlayerNews for volatility keywords.
        
        Only considers news from the last 48 hours.
        Filters out rumors and speculation.
        """
        result = {
            "has_volatility": False,
            "reason": None,
            "source": None,
            "headline": None,
            "articles_scanned": 0
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Fetch player news
                url = f"{TANK01_BASE}/getNBAPlayerNews"
                response = await client.get(
                    url,
                    params={"playerName": player_name},
                    headers=self.headers
                )
                
                if response.status_code != 200:
                    return result
                
                data = response.json()
                body = data.get("body", [])
                
                if not body or not isinstance(body, list):
                    return result
                
                # Get cutoff time (48 hours ago)
                cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
                
                for article in body:
                    result["articles_scanned"] += 1
                    
                    # Check article date (if available)
                    article_date_str = article.get("publishedAt") or article.get("date")
                    if article_date_str:
                        try:
                            # Try parsing date
                            article_date = datetime.fromisoformat(article_date_str.replace("Z", "+00:00"))
                            if article_date < cutoff:
                                continue  # Skip old articles
                        except (ValueError, TypeError):
                            pass  # If can't parse, still check the article
                    
                    # Get title and body text
                    title = (article.get("title") or "").lower()
                    body_text = (article.get("body") or article.get("description") or "").lower()
                    combined_text = f"{title} {body_text}"
                    
                    # VERIFICATION RULE: Skip rumors and speculation
                    is_rumor = any(kw in combined_text for kw in RUMOR_KEYWORDS)
                    if is_rumor:
                        continue
                    
                    # Check for REDUCED USAGE keywords
                    for keyword in REDUCED_USAGE_KEYWORDS:
                        if keyword in combined_text:
                            result["has_volatility"] = True
                            result["reason"] = f"REDUCED USAGE: {keyword.upper()}"
                            result["source"] = article.get("source", {}).get("name", "Unknown")
                            result["headline"] = article.get("title", "")[:100]
                            return result
                    
                    # Check for VOLATILITY keywords
                    for keyword in VOLATILITY_KEYWORDS:
                        if keyword in combined_text:
                            result["has_volatility"] = True
                            result["reason"] = f"VOLATILITY: {keyword.upper()}"
                            result["source"] = article.get("source", {}).get("name", "Unknown")
                            result["headline"] = article.get("title", "")[:100]
                            return result
                
        except Exception as e:
            logger.debug(f"[SOCIAL SIGNAL] News check error for {player_name}: {e}")
        
        return result
    
    async def _check_revenge_game(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Check if player is facing a former team (revenge game).
        
        Uses:
        - getNBAPlayerInfo for previousTeams array
        - Current game schedule for opponent matching
        """
        result = {
            "is_revenge": False,
            "opponent": None,
            "previous_team": None
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Step 1: Get player info including previous teams
                player_url = f"{TANK01_BASE}/getNBAPlayerInfo"
                player_response = await client.get(
                    player_url,
                    params={"playerName": player_name},
                    headers=self.headers
                )
                
                if player_response.status_code != 200:
                    return result
                
                player_data = player_response.json()
                body = player_data.get("body", [])
                
                if not body or not isinstance(body, list):
                    return result
                
                player = body[0]
                current_team = player.get("team")
                previous_teams = player.get("previousTeams", [])
                
                if not previous_teams or not current_team:
                    return result
                
                # Step 2: Get today's opponent from team schedule
                today = datetime.now().strftime("%Y%m%d")
                schedule_url = f"{TANK01_BASE}/getNBAGamesForDate"
                schedule_response = await client.get(
                    schedule_url,
                    params={"gameDate": today},
                    headers=self.headers
                )
                
                if schedule_response.status_code != 200:
                    return result
                
                schedule_data = schedule_response.json()
                games = schedule_data.get("body", [])
                
                # Find the game for this player's team
                opponent = None
                for game in games:
                    home = game.get("home")
                    away = game.get("away")
                    
                    if home == current_team:
                        opponent = away
                        break
                    elif away == current_team:
                        opponent = home
                        break
                
                if not opponent:
                    return result
                
                # Step 3: Check if opponent is in previousTeams
                # previousTeams might be team abbreviations or full names
                for prev_team in previous_teams:
                    prev_team_str = str(prev_team).upper()
                    opponent_str = str(opponent).upper()
                    
                    if prev_team_str == opponent_str or prev_team_str in opponent_str or opponent_str in prev_team_str:
                        result["is_revenge"] = True
                        result["opponent"] = opponent
                        result["previous_team"] = prev_team
                        logger.info(f"[REVENGE GAME] {player_name} vs former team {opponent}")
                        return result
                
        except Exception as e:
            logger.debug(f"[SOCIAL SIGNAL] Revenge check error for {player_name}: {e}")
        
        return result
    
    async def get_player_signal(self, player_name: str) -> Optional[Dict[str, Any]]:
        """
        Get the cached social signal for a player.
        
        Returns None if no signal exists.
        """
        signal = await self.social_signals.find_one(
            {"player_name": player_name},
            {"_id": 0}
        )
        return signal
    
    async def get_all_signals(self) -> Dict[str, Any]:
        """
        Get all cached social signals.
        
        Returns dict with player_name as key for fast lookup.
        """
        signals = await self.social_signals.find({}, {"_id": 0}).to_list(None)
        
        # Convert to dict for fast lookup
        signals_dict = {}
        for signal in signals:
            signals_dict[signal["player_name"]] = signal
        
        return {
            "success": True,
            "signals": signals_dict,
            "total_volatility": sum(1 for s in signals if s.get("volatility_flag")),
            "total_revenge": sum(1 for s in signals if s.get("revenge_game")),
            "last_sync": self.last_sync.isoformat() if self.last_sync else None
        }
    
    async def apply_signals_to_board(self) -> Dict[str, Any]:
        """
        Apply social signals to the cached board players.
        
        Updates player documents with:
        - volatility_flag
        - revenge_game
        - gem_modifier (for adjusting confidence)
        """
        results = {
            "updated": 0,
            "volatility_applied": 0,
            "revenge_applied": 0
        }
        
        try:
            signals = await self.get_all_signals()
            signals_dict = signals.get("signals", {})
            
            for player_name, signal in signals_dict.items():
                gem_modifier = 0
                
                # Volatility decreases confidence by 1 gem
                if signal.get("volatility_flag"):
                    gem_modifier -= 1
                    results["volatility_applied"] += 1
                
                # Revenge game increases confidence by 1 gem
                if signal.get("revenge_game"):
                    gem_modifier += 1
                    results["revenge_applied"] += 1
                
                # Update the cached board
                update_result = await self.db.dg_cached_board.update_one(
                    {"player_name": player_name},
                    {"$set": {
                        "volatility_flag": signal.get("volatility_flag", False),
                        "volatility_reason": signal.get("volatility_reason"),
                        "revenge_game": signal.get("revenge_game", False),
                        "revenge_opponent": signal.get("revenge_opponent"),
                        "gem_modifier": gem_modifier
                    }}
                )
                
                if update_result.modified_count > 0:
                    results["updated"] += 1
            
            logger.info(f"[SOCIAL SIGNAL] Applied signals to {results['updated']} players")
            
        except Exception as e:
            logger.error(f"[SOCIAL SIGNAL] Error applying signals: {e}")
            results["error"] = str(e)
        
        return results


# Singleton instance
_social_signal_engine = None

def get_social_signal_engine(db) -> SocialSignalEngine:
    """Get or create the SocialSignalEngine singleton."""
    global _social_signal_engine
    if _social_signal_engine is None:
        _social_signal_engine = SocialSignalEngine(db)
    return _social_signal_engine
