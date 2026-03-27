"""
Injury Intelligence Service - Real-time NBA injury tracking and usage ripple analysis
Primary: ESPN Injuries API (reliable, comprehensive)
Secondary: BallDontLie API (BDL) for official NBA injury data

Features:
- Real-time injury status tracking
- "Usage Ripple" analysis (who benefits when a star is out)
- Risk flagging for GTD/Questionable players
- Breaking news detection

NOTE: Tank01 has been REMOVED from this application.
"""

import os
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import httpx

load_dotenv()

logger = logging.getLogger(__name__)

# API Configuration
ESPN_INJURIES_URL = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
ESPN_NEWS_URL = "http://site.api.espn.com/apis/site/v2/sports/basketball/nba/news"

# Injury status severity mapping
INJURY_SEVERITY = {
    "Out": {"level": 3, "color": "red", "risk": "HIGH"},
    "Doubtful": {"level": 2.5, "color": "red", "risk": "HIGH"},
    "Questionable": {"level": 2, "color": "yellow", "risk": "MEDIUM"},
    "Probable": {"level": 1, "color": "green", "risk": "LOW"},
    "Day-To-Day": {"level": 2, "color": "yellow", "risk": "MEDIUM"},
    "GTD": {"level": 2, "color": "yellow", "risk": "MEDIUM"},
}


class InjuryIntelligenceService:
    """
    The Injury Intelligence "War Room" - Tracks injuries and calculates usage ripples.
    """
    
    def __init__(self, db):
        """Initialize with MongoDB database connection."""
        self.db = db
        self.injuries_collection = db.dg_injuries
        self.cached_board = db.dg_cached_board
        self.daily_insights = db.dg_daily_insights
        self.breaking_news = db.dg_breaking_news
        
    async def sync_injuries(self) -> Dict[str, Any]:
        """
        Sync injury data from ESPN API + Tank01 enrichment.
        Updates the dg_injuries collection with current injury status.
        """
        logger.info("[INJURY] Starting injury sync from ESPN + Tank01...")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(ESPN_INJURIES_URL)
                response.raise_for_status()
                data = response.json()
            
            if data.get('status') != 'success':
                raise Exception(f"ESPN API error: {data.get('status')}")
            
            injuries_data = data.get('injuries', [])
            synced_at = datetime.now(timezone.utc)
            
            all_injuries = []
            injured_players = []
            
            for team_data in injuries_data:
                team_name = team_data.get('displayName', 'Unknown')
                team_abbr = self._get_team_abbr(team_name)
                
                for injury in team_data.get('injuries', []):
                    athlete = injury.get('athlete', {})
                    player_name = athlete.get('displayName', 'Unknown')
                    status = injury.get('status', 'Unknown')
                    
                    injury_record = {
                        "player_name": player_name,
                        "player_id": athlete.get('id'),
                        "team": team_abbr,
                        "team_full": team_name,
                        "status": status,
                        "injury_type": injury.get('type', {}).get('description', 'Unknown'),
                        "description": injury.get('longComment', ''),
                        "short_comment": injury.get('shortComment', ''),
                        "severity": INJURY_SEVERITY.get(status, INJURY_SEVERITY['Questionable']),
                        "date_reported": injury.get('date', synced_at.isoformat()),
                        "synced_at": synced_at.isoformat(),
                        "source": "ESPN"
                    }
                    
                    all_injuries.append(injury_record)
                    
                    # Track for usage ripple calculation
                    if status in ['Out', 'Doubtful']:
                        injured_players.append({
                            "player_name": player_name,
                            "team": team_abbr,
                            "status": status
                        })
            
            # Sync from BDL for comprehensive injury coverage
            bdl_count = await self._sync_bdl_injuries()
            
            # Clear and insert fresh injury data
            await self.injuries_collection.delete_many({})
            if all_injuries:
                await self.injuries_collection.insert_many(all_injuries)
            
            # Calculate usage ripples for injured star players
            ripple_updates = await self._calculate_usage_ripples(injured_players)
            
            # Fetch breaking news
            breaking_count = await self._sync_breaking_news()
            
            logger.info(f"[INJURY] Synced {len(all_injuries)} injuries, {bdl_count} BDL injuries, {ripple_updates} ripple updates")
            
            return {
                "success": True,
                "injuries_synced": len(all_injuries),
                "bdl_injuries": bdl_count,
                "teams_affected": len(injuries_data),
                "star_players_out": len(injured_players),
                "usage_ripple_updates": ripple_updates,
                "breaking_news_items": breaking_count,
                "synced_at": synced_at.isoformat()
            }
            
        except Exception as e:
            logger.error(f"[INJURY] Sync failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _calculate_usage_ripples(self, injured_players: List[Dict]) -> int:
        """
        Calculate usage bump for teammates of injured star players.
        When a high-usage player is out, their teammates get more opportunities.
        
        TRIGGER-BASED CACHE INVALIDATION:
        After updating daily_insights, we invalidate the cached_board to force
        a rebuild with the fresh usage_bump data on next request.
        """
        updates = 0
        teams_affected = set()
        
        for injured in injured_players:
            team = injured['team']
            injured_name = injured['player_name']
            teams_affected.add(team)
            
            # Check if this is a high-usage player (star) and get their usage rate
            injured_insight = await self.daily_insights.find_one(
                {"player_name": injured_name},
                {"_id": 0, "usage_rate": 1}
            )
            
            # Use actual usage rate if available, otherwise default to moderate
            base_usage = injured_insight.get("usage_rate", 20) if injured_insight else 20
            
            # Find teammates and bump their usage
            teammates = await self.cached_board.find(
                {"team_abbreviation": team, "player_name": {"$ne": injured_name}},
                {"_id": 0, "player_name": 1}
            ).to_list(50)
            
            for teammate in teammates:
                # Calculate usage bump (simplified: distribute injured player's usage)
                # In reality, this would be more sophisticated based on position/role
                usage_bump = base_usage * 0.15  # 15% of injured player's usage distributed
                
                # Update teammate's insights with usage bump and injury context
                await self.daily_insights.update_one(
                    {"player_name": teammate['player_name']},
                    {
                        "$set": {
                            "usage_bump_percent": usage_bump,
                            "usage_bump_reason": f"{injured_name} is {injured['status']}",
                            "injured_teammates": [injured_name],
                            "ripple_detected": True,
                            "ripple_updated_at": datetime.now(timezone.utc)
                        }
                    }
                )
                updates += 1
        
        # CACHE INVALIDATION: If any ripples were detected, invalidate the cached board
        # This forces next request to rebuild with fresh usage_bump data
        if updates > 0:
            logger.info(f"[RIPPLE] {updates} usage bumps applied. Invalidating cached board for teams: {teams_affected}")
            
            # Clear the cached board entries for affected teams to force rebuild
            for team in teams_affected:
                await self.cached_board.update_many(
                    {"team_abbreviation": team},
                    {
                        "$set": {
                            "cache_invalidated": True,
                            "invalidation_reason": "usage_ripple_update",
                            "invalidated_at": datetime.now(timezone.utc)
                        }
                    }
                )
            
            # Also update the cache metadata to signal a rebuild is needed
            await self.db.dg_cache_meta.update_one(
                {"key": "cached_board"},
                {
                    "$set": {
                        "ripple_invalidated": True,
                        "ripple_invalidated_at": datetime.now(timezone.utc),
                        "teams_affected": list(teams_affected)
                    }
                },
                upsert=True
            )
        
        return updates
    
    async def _sync_bdl_injuries(self) -> int:
        """
        Sync injury reports from BallDontLie API.
        BDL provides comprehensive injury data directly from official NBA sources.
        """
        try:
            from services.bdl_enhanced_data import get_bdl_enhanced_service
            
            bdl_service = get_bdl_enhanced_service(self.db)
            result = await bdl_service.sync_injuries()
            
            if result.get('success'):
                logger.info(f"[INJURY] BDL sync: {result.get('injuries_count', 0)} injuries, {result.get('players_updated', 0)} context updates")
                return result.get('injuries_count', 0)
            else:
                logger.warning(f"[INJURY] BDL sync failed: {result.get('error', 'Unknown error')}")
                return 0
        except Exception as e:
            logger.error(f"[INJURY] BDL sync error: {e}")
            return 0
    
    async def get_team_injury_summary(self, team_abbr: str) -> str:
        """
        Get a summary string of team injuries for Vision AI context.
        """
        injuries = await self.get_team_injuries(team_abbr)
        
        if not injuries:
            return ""
        
        out_players = [i['player_name'] for i in injuries if i.get('status') == 'Out']
        questionable = [i['player_name'] for i in injuries if i.get('status') in ['Questionable', 'Day-To-Day', 'GTD']]
        
        parts = []
        if out_players:
            parts.append(f"OUT: {', '.join(out_players[:3])}")
        if questionable:
            parts.append(f"GTD: {', '.join(questionable[:2])}")
        
        return "; ".join(parts) if parts else ""
    
    async def _sync_breaking_news(self) -> int:
        """Sync breaking NBA news from ESPN."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(ESPN_NEWS_URL)
                response.raise_for_status()
                data = response.json()
            
            articles = data.get('articles', [])
            synced_at = datetime.now(timezone.utc)
            
            news_items = []
            for article in articles[:20]:  # Keep last 20 articles
                # Check if injury-related
                headline = article.get('headline', '').lower()
                description = article.get('description', '').lower()
                
                is_injury_related = any(word in headline + description for word in [
                    'injury', 'injured', 'out', 'questionable', 'doubtful',
                    'return', 'ruled out', 'day-to-day', 'gtd', 'miss'
                ])
                
                news_item = {
                    "headline": article.get('headline', ''),
                    "description": article.get('description', ''),
                    "published": article.get('published', ''),
                    "link": article.get('links', {}).get('web', {}).get('href', ''),
                    "is_injury_related": is_injury_related,
                    "is_breaking": article.get('type') == 'HeadlineNews',
                    "synced_at": synced_at.isoformat()
                }
                news_items.append(news_item)
            
            # Clear and insert fresh news
            await self.breaking_news.delete_many({})
            if news_items:
                await self.breaking_news.insert_many(news_items)
            
            return len([n for n in news_items if n['is_injury_related']])
            
        except Exception as e:
            logger.error(f"[INJURY] Breaking news sync failed: {e}")
            return 0
    
    async def get_player_injury_status(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get injury status for a specific player."""
        injury = await self.injuries_collection.find_one(
            {"player_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        return injury
    
    async def get_team_injuries(self, team_abbr: str) -> List[Dict[str, Any]]:
        """Get all injuries for a specific team."""
        cursor = self.injuries_collection.find(
            {"team": team_abbr.upper()},
            {"_id": 0}
        )
        return await cursor.to_list(50)
    
    async def get_all_injuries(self) -> Dict[str, Any]:
        """Get all current injuries grouped by severity from both ESPN and BDL sources."""
        # Get ESPN injuries (dg_injuries)
        espn_cursor = self.injuries_collection.find({}, {"_id": 0})
        espn_injuries = await espn_cursor.to_list(500)
        
        # Get BDL injuries (bdl_injuries) - from BallDontLie API
        bdl_cursor = self.db.bdl_injuries.find({}, {"_id": 0})
        bdl_injuries = await bdl_cursor.to_list(500)
        
        # Combine and deduplicate by player_name (prefer BDL data as it's more official)
        injuries_by_player = {}
        
        # Add ESPN injuries first
        for injury in espn_injuries:
            player_name = injury.get('player_name')
            if player_name:
                # Normalize severity format
                severity = injury.get('severity', {})
                risk = severity.get('risk', 'MEDIUM') if isinstance(severity, dict) else 'MEDIUM'
                injuries_by_player[player_name.lower()] = {
                    **injury,
                    "source": "espn",
                    "risk_level": risk
                }
        
        # Add/override with BDL injuries
        for injury in bdl_injuries:
            player_name = injury.get('player_name')
            if player_name:
                # Map BDL severity to risk level
                severity = injury.get('severity', 'unknown')
                if severity in ['out', 'season_ending']:
                    risk = 'HIGH'
                elif severity in ['doubtful', 'questionable']:
                    risk = 'MEDIUM'
                else:
                    risk = 'LOW'
                
                injuries_by_player[player_name.lower()] = {
                    "player_name": player_name,
                    "team": injury.get('team'),
                    "status": injury.get('status'),
                    "injury_type": injury.get('injury_type'),
                    "return_date": injury.get('return_date'),
                    "bdl_id": injury.get('bdl_id'),
                    "source": "bdl",
                    "risk_level": risk,
                    "severity": {"risk": risk}  # Normalized format
                }
        
        all_injuries = list(injuries_by_player.values())
        
        # Group by risk level
        high_risk = [i for i in all_injuries if i.get('risk_level') == 'HIGH']
        medium_risk = [i for i in all_injuries if i.get('risk_level') == 'MEDIUM']
        low_risk = [i for i in all_injuries if i.get('risk_level') == 'LOW']
        
        return {
            "success": True,
            "total_injuries": len(all_injuries),
            "high_risk": high_risk,
            "medium_risk": medium_risk,
            "low_risk": low_risk,
            "high_risk_count": len(high_risk),
            "medium_risk_count": len(medium_risk),
            "low_risk_count": len(low_risk)
        }
    
    async def get_breaking_news(self, injury_only: bool = False) -> List[Dict[str, Any]]:
        """Get breaking news, optionally filtered to injury-related only."""
        query = {"is_injury_related": True} if injury_only else {}
        cursor = self.breaking_news.find(query, {"_id": 0}).sort("published", -1).limit(10)
        return await cursor.to_list(10)
    
    async def get_injury_alerts_for_board(self) -> Dict[str, Dict[str, Any]]:
        """
        Get injury alerts formatted for the dashboard board.
        Returns a dict mapping player_name -> injury_info for quick lookup.
        Combines ESPN and BDL injury data.
        """
        alerts = {}
        
        # Get ESPN injuries
        espn_cursor = self.injuries_collection.find({}, {"_id": 0})
        espn_injuries = await espn_cursor.to_list(500)
        
        for injury in espn_injuries:
            player_name = injury.get('player_name')
            if player_name:
                alerts[player_name] = {
                    "status": injury.get('status'),
                    "severity": injury.get('severity', {}).get('risk', 'MEDIUM'),
                    "color": injury.get('severity', {}).get('color', 'yellow'),
                    "description": injury.get('short_comment') or injury.get('description', '')[:100],
                    "team": injury.get('team'),
                    "source": "espn"
                }
        
        # Get BDL injuries (override ESPN with more official data)
        bdl_cursor = self.db.bdl_injuries.find({}, {"_id": 0})
        bdl_injuries = await bdl_cursor.to_list(500)
        
        for injury in bdl_injuries:
            player_name = injury.get('player_name')
            if player_name:
                severity = injury.get('severity', 'unknown')
                if severity in ['out', 'season_ending']:
                    risk = 'HIGH'
                    color = 'red'
                elif severity in ['doubtful', 'questionable']:
                    risk = 'MEDIUM'
                    color = 'yellow'
                else:
                    risk = 'LOW'
                    color = 'green'
                
                alerts[player_name] = {
                    "status": injury.get('status'),
                    "severity": risk,
                    "color": color,
                    "description": injury.get('injury_type') or f"Return: {injury.get('return_date', 'TBD')}",
                    "team": injury.get('team'),
                    "source": "bdl"
                }
        
        return alerts
    
    def _get_team_abbr(self, team_name: str) -> str:
        """Convert full team name to abbreviation."""
        team_map = {
            "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
            "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
            "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
            "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
            "LA Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
            "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
            "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
            "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
            "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
            "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS"
        }
        return team_map.get(team_name, team_name[:3].upper())


# Singleton instance holder
_injury_service: Optional[InjuryIntelligenceService] = None


def get_injury_service(db) -> InjuryIntelligenceService:
    """Get or create the Injury Intelligence service singleton."""
    global _injury_service
    if _injury_service is None:
        _injury_service = InjuryIntelligenceService(db)
    return _injury_service
