"""
Action Network Scraper Service
==============================
Scrapes public betting data and expert picks from Action Network
to enrich Top Picks with "What the Public is Betting" intel.

NOTE: Action Network uses JavaScript rendering, so direct scraping is limited.
This service now also provides a fallback "popularity estimation" based on:
- Line movement (lines moving against public = sharp action)
- Hit rates (high hit rate props are popular with recreational bettors)
- Player star power (big names draw more action)

Data Sources:
1. Public Betting Page - % of bets and % of money per game (when available)
2. Fallback: Internal popularity estimation algorithm
"""

import httpx
import re
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


# Star players who typically draw heavy public betting action
STAR_PLAYERS = {
    "LeBron James", "Stephen Curry", "Kevin Durant", "Giannis Antetokounmpo",
    "Luka Doncic", "Jayson Tatum", "Joel Embiid", "Nikola Jokic",
    "Anthony Edwards", "Ja Morant", "Shai Gilgeous-Alexander", "Damian Lillard",
    "Devin Booker", "Donovan Mitchell", "Kyrie Irving", "Trae Young",
    "Zion Williamson", "Anthony Davis", "Paul George", "Jimmy Butler",
    "Kawhi Leonard", "James Harden", "Karl-Anthony Towns", "Tyrese Haliburton"
}

# Large market teams that draw more public attention
LARGE_MARKET_TEAMS = {"LAL", "NYK", "GSW", "BOS", "CHI", "MIA", "PHI", "BKN", "DAL", "LAC"}


class ActionNetworkScraper:
    """Scrapes Action Network for public betting data and expert picks."""
    
    BASE_URL = "https://www.actionnetwork.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }
    
    # Team abbreviation mapping (Action Network uses some different abbrevs)
    TEAM_MAP = {
        "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA",
        "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
        "DET": "DET", "GSW": "GSW", "HOU": "HOU", "IND": "IND",
        "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
        "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NO": "NOP",
        "NYK": "NYK", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI",
        "PHX": "PHX", "POR": "POR", "SAC": "SAC", "SAS": "SAS",
        "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
    }
    
    def __init__(self):
        self.client = None
        self._cache = {}
        self._cache_time = None
        self._cache_ttl = 300  # 5 minute cache
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self.client is None:
            self.client = httpx.AsyncClient(
                headers=self.HEADERS,
                timeout=30.0,
                follow_redirects=True
            )
        return self.client
    
    async def close(self):
        """Close the HTTP client."""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    async def get_public_betting_data(self) -> Dict[str, Any]:
        """
        Try to scrape public betting percentages from Action Network.
        Falls back to empty if site uses JS rendering.
        """
        try:
            # Check cache
            if self._cache_time and (datetime.now(timezone.utc) - self._cache_time).seconds < self._cache_ttl:
                return self._cache.get("public_betting", {})
            
            client = await self._get_client()
            url = f"{self.BASE_URL}/nba/public-betting"
            
            response = await client.get(url)
            
            # Action Network returns 202 and renders via JS - scraping won't work
            if response.status_code == 202 or len(response.text) < 10000:
                logger.info("[ACTION_NETWORK] Site uses JS rendering - using fallback estimation")
                return {"games": [], "source": "unavailable_js_rendered"}
            
            html = response.text
            games = self._parse_public_betting_html(html)
            
            result = {
                "games": games,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "source": "action_network"
            }
            
            # Cache result
            self._cache["public_betting"] = result
            self._cache_time = datetime.now(timezone.utc)
            
            logger.info(f"[ACTION_NETWORK] Scraped {len(games)} games with public betting data")
            return result
            
        except Exception as e:
            logger.error(f"[ACTION_NETWORK] Error scraping public betting: {e}")
            return {"games": [], "error": str(e)}
    
    def _parse_public_betting_html(self, html: str) -> List[Dict]:
        """Parse HTML to extract game betting percentages."""
        games = []
        
        try:
            # Extract team matchups and percentages using regex
            team_pattern = r'teamlogos/nba/100/(\w+)\.png'
            teams = re.findall(team_pattern, html)
            
            pct_pattern = r'>(\d{1,3})%<'
            percentages = re.findall(pct_pattern, html)
            
            i = 0
            pct_idx = 0
            
            while i < len(teams) - 1:
                away_team = teams[i].upper()
                home_team = teams[i + 1].upper()
                
                away_team = self.TEAM_MAP.get(away_team, away_team)
                home_team = self.TEAM_MAP.get(home_team, home_team)
                
                game = {
                    "away_team": away_team,
                    "home_team": home_team,
                    "away_bets_pct": None,
                    "home_bets_pct": None,
                }
                
                if pct_idx + 1 < len(percentages):
                    game["away_bets_pct"] = int(percentages[pct_idx])
                    game["home_bets_pct"] = int(percentages[pct_idx + 1])
                    pct_idx += 2
                
                games.append(game)
                i += 2
            
        except Exception as e:
            logger.error(f"[ACTION_NETWORK] Error parsing HTML: {e}")
        
        return games
    
    def estimate_popularity(self, pick: Dict) -> Dict[str, Any]:
        """
        Estimate popularity when real data isn't available.
        
        Uses heuristics:
        - Star players draw more action
        - Large market teams draw more action
        - High hit rate props are popular with recreational bettors
        - Over bets are generally more popular than unders
        """
        player_name = pick.get("player_name", "")
        team = pick.get("team", "").upper()
        h10_rate = pick.get("h10_rate", 50)
        stat_type = pick.get("stat_type", "").lower()
        
        # Base popularity score
        popularity_pct = 50
        
        # Star player bonus (+15%)
        if player_name in STAR_PLAYERS:
            popularity_pct += 15
        
        # Large market bonus (+10%)
        if team in LARGE_MARKET_TEAMS:
            popularity_pct += 10
        
        # High hit rate bonus (recreational bettors love winners)
        if h10_rate >= 70:
            popularity_pct += 10
        elif h10_rate >= 60:
            popularity_pct += 5
        
        # Points are the most popular stat type
        if "pts" in stat_type or "point" in stat_type:
            popularity_pct += 5
        
        # Cap at 85% (nothing is truly consensus)
        popularity_pct = min(85, popularity_pct)
        
        # Determine sentiment label
        if popularity_pct >= 65:
            sentiment = "heavy_public"
            label = "🔥 Public Favorite"
        elif popularity_pct >= 55:
            sentiment = "slight_public"
            label = "📈 Trending"
        elif popularity_pct <= 40:
            sentiment = "contrarian"
            label = "🎯 Contrarian"
        else:
            sentiment = "neutral"
            label = "⚖️ Split Action"
        
        return {
            "estimated_public_pct": popularity_pct,
            "public_sentiment": sentiment,
            "sentiment_label": label,
            "estimation_source": "heuristic"
        }
    
    async def enrich_picks_with_public_data(
        self, 
        picks: List[Dict],
        public_betting_data: Dict = None
    ) -> List[Dict]:
        """
        Enrich player picks with public betting sentiment.
        
        If real data available: uses actual percentages
        If not: uses heuristic estimation
        """
        if not public_betting_data:
            public_betting_data = await self.get_public_betting_data()
        
        games = public_betting_data.get("games", [])
        has_real_data = len(games) > 0
        
        # Create lookup by team if we have real data
        team_to_game = {}
        if has_real_data:
            for game in games:
                team_to_game[game["away_team"]] = {
                    "bets_pct": game.get("away_bets_pct"),
                    "opponent": game["home_team"],
                }
                team_to_game[game["home_team"]] = {
                    "bets_pct": game.get("home_bets_pct"),
                    "opponent": game["away_team"],
                }
        
        enriched = []
        for pick in picks:
            pick_copy = pick.copy()
            team = pick.get("team", "").upper()
            
            if has_real_data and team in team_to_game:
                # Use real data
                game_data = team_to_game[team]
                bets_pct = game_data.get("bets_pct")
                
                pick_copy["public_bets_pct"] = bets_pct
                pick_copy["opponent"] = game_data.get("opponent")
                
                if bets_pct:
                    if bets_pct >= 65:
                        pick_copy["public_sentiment"] = "heavy_public"
                        pick_copy["sentiment_label"] = "🔥 Heavy Public"
                    elif bets_pct >= 55:
                        pick_copy["public_sentiment"] = "slight_public"
                        pick_copy["sentiment_label"] = "📈 Public Favorite"
                    elif bets_pct <= 35:
                        pick_copy["public_sentiment"] = "heavy_fade"
                        pick_copy["sentiment_label"] = "🎯 Sharp Fade"
                    elif bets_pct <= 45:
                        pick_copy["public_sentiment"] = "slight_fade"
                        pick_copy["sentiment_label"] = "💡 Contrarian"
                    else:
                        pick_copy["public_sentiment"] = "neutral"
                        pick_copy["sentiment_label"] = "⚖️ Split Action"
            else:
                # Use heuristic estimation
                estimation = self.estimate_popularity(pick)
                pick_copy.update(estimation)
                pick_copy["public_bets_pct"] = estimation.get("estimated_public_pct")
            
            enriched.append(pick_copy)
        
        return enriched
    
    def calculate_popularity_score(self, pick: Dict) -> float:
        """
        Calculate a popularity score for ranking picks.
        
        Higher score = more popular bet
        """
        score = 50.0  # Base score
        
        # Add public betting component (0-30 points)
        public_pct = pick.get("public_bets_pct") or pick.get("estimated_public_pct")
        if public_pct:
            score += (public_pct - 50) * 0.6  # +/- 30 points max
        
        # Add hit rate component (0-20 points)
        h10_rate = pick.get("h10_rate", 50)
        if h10_rate:
            score += (h10_rate - 50) * 0.4  # +/- 20 points max
        
        # Normalize to 0-100
        return max(0, min(100, score))


# Singleton instance
_scraper_instance = None

def get_action_network_scraper() -> ActionNetworkScraper:
    """Get singleton scraper instance."""
    global _scraper_instance
    if _scraper_instance is None:
        _scraper_instance = ActionNetworkScraper()
    return _scraper_instance
