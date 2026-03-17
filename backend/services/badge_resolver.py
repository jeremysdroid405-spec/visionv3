"""
Badge Resolver Service
======================
Maps narrative flags and context data to standardized player badges.

BADGE DEFINITIONS (10 Reusable Badges):
1. [Jet Lag]       - Travel > 1000mi
2. [Gassed]        - Back-to-back game
3. [Home Cookin']  - Home game + 10% PPG split
4. [Legal Noise]   - Divorce, custody, or legal filings
5. [Distraction]   - Trade rumors/off-court drama
6. [Revenge]       - Former team matchup
7. [Pay Day]       - Contract year
8. [Milestone]     - Chasing records
9. [Deep Water]    - Playoff/Elimination stakes
10. [Locked In]    - High performance despite distractions
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Badge definitions with their trigger conditions
BADGE_DEFINITIONS = {
    "jet_lag": {
        "display": "Jet Lag",
        "icon": "plane",
        "color": "#6366f1",  # Indigo
        "description": "Long travel distance (>1000mi)",
        "trigger_flags": ["travel", "road_game"],
        "min_travel_miles": 1000
    },
    "gassed": {
        "display": "Gassed",
        "icon": "battery-low",
        "color": "#ef4444",  # Red
        "description": "Back-to-back game",
        "trigger_flags": ["back_to_back", "b2b"]
    },
    "home_cookin": {
        "display": "Home Cookin'",
        "icon": "home",
        "color": "#22c55e",  # Green
        "description": "Home game advantage",
        "trigger_flags": ["home_game"],
        "requires_ppg_split": 0.10  # 10% PPG boost at home
    },
    "legal_noise": {
        "display": "Legal Noise",
        "icon": "gavel",
        "color": "#f59e0b",  # Amber
        "description": "Legal proceedings or filings",
        "trigger_flags": ["legal", "divorce", "custody", "legal_custody_battle", "lawsuit"]
    },
    "distraction": {
        "display": "Distraction",
        "icon": "alert-triangle",
        "color": "#f97316",  # Orange
        "description": "Off-court drama or trade rumors",
        "trigger_flags": ["trade_rumors", "drama", "controversy", "off_court"]
    },
    "revenge": {
        "display": "Revenge",
        "icon": "target",
        "color": "#dc2626",  # Red-600
        "description": "Playing former team",
        "trigger_flags": ["revenge", "former_team", "revenge_game"]
    },
    "pay_day": {
        "display": "Pay Day",
        "icon": "dollar-sign",
        "color": "#16a34a",  # Green-600
        "description": "Contract year motivation",
        "trigger_flags": ["contract_year", "pay_day", "free_agent"]
    },
    "milestone": {
        "display": "Milestone",
        "icon": "trophy",
        "color": "#eab308",  # Yellow-500
        "description": "Chasing records or achievements",
        "trigger_flags": ["milestone", "record", "scoring_leader", "triple_double_watch"]
    },
    "deep_water": {
        "display": "Deep Water",
        "icon": "flame",
        "color": "#7c3aed",  # Violet-600
        "description": "High-stakes playoff or elimination game",
        "trigger_flags": ["playoff", "elimination", "must_win", "playoff_push"]
    },
    "locked_in": {
        "display": "Locked In",
        "icon": "lock",
        "color": "#0ea5e9",  # Sky-500
        "description": "Elite focus despite distractions",
        "trigger_flags": ["locked_in", "focused", "elite_focus"]
    }
}

# Flag type to badge mapping
FLAG_TO_BADGE_MAP = {
    # Legal
    "legal": "legal_noise",
    "divorce": "legal_noise",
    "custody": "legal_noise",
    "legal_custody_battle": "legal_noise",
    "lawsuit": "legal_noise",
    
    # Travel
    "travel": "jet_lag",
    "road_game": "jet_lag",
    
    # Fatigue
    "back_to_back": "gassed",
    "b2b": "gassed",
    
    # Home advantage
    "home_game": "home_cookin",
    
    # Distractions
    "trade_rumors": "distraction",
    "drama": "distraction",
    "controversy": "distraction",
    "off_court": "distraction",
    
    # Revenge
    "revenge": "revenge",
    "former_team": "revenge",
    "revenge_game": "revenge",
    
    # Contract
    "contract_year": "pay_day",
    "pay_day": "pay_day",
    "free_agent": "pay_day",
    
    # Milestones
    "milestone": "milestone",
    "record": "milestone",
    "scoring_leader": "milestone",
    "triple_double_watch": "milestone",
    
    # Playoffs
    "playoff": "deep_water",
    "elimination": "deep_water",
    "must_win": "deep_water",
    "playoff_push": "deep_water",
    
    # Focus
    "locked_in": "locked_in",
    "focused": "locked_in",
    "elite_focus": "locked_in"
}


class BadgeResolverService:
    """
    Resolves player context flags into displayable badges.
    
    Fetches flags from nba_context_engine collection and maps them
    to standardized badge definitions.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.context_engine = db.nba_context_engine
        self.master_hub = db.nba_master_hub_2026
    
    async def get_player_flags(
        self, 
        player_id: int,
        days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Get active narrative flags for a player.
        
        Args:
            player_id: NBA player ID
            days_back: How many days back to look for flags
            
        Returns:
            List of active flag documents
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        cursor = self.context_engine.find({
            "player_id": player_id,
            "timestamp": {"$gte": cutoff.isoformat()},
            "active": {"$ne": False}
        }).sort("severity", -1)
        
        flags = await cursor.to_list(50)
        return flags
    
    def _resolve_badge_from_flag(self, flag: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Resolve a single flag to a badge definition.
        
        Returns badge dict with display info or None if no match.
        """
        flag_type = flag.get("flag_type", "").lower()
        
        # Direct mapping
        badge_key = FLAG_TO_BADGE_MAP.get(flag_type)
        
        if not badge_key:
            # Try partial matching
            for known_flag, badge in FLAG_TO_BADGE_MAP.items():
                if known_flag in flag_type or flag_type in known_flag:
                    badge_key = badge
                    break
        
        if not badge_key:
            return None
        
        badge_def = BADGE_DEFINITIONS.get(badge_key, {})
        
        # Check travel distance for jet_lag badge
        if badge_key == "jet_lag":
            travel_miles = flag.get("travel_miles", 0)
            if travel_miles < badge_def.get("min_travel_miles", 1000):
                return None
        
        return {
            "badge_key": badge_key,
            "display": badge_def.get("display", badge_key),
            "icon": badge_def.get("icon", "info"),
            "color": badge_def.get("color", "#6b7280"),
            "description": badge_def.get("description", ""),
            "severity": flag.get("severity", 5),
            "headline": flag.get("headline_reference", ""),
            "source_flag": flag_type
        }
    
    async def resolve_badges(
        self,
        player_id: int,
        include_auto_badges: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Resolve all active badges for a player.
        
        Args:
            player_id: NBA player ID
            include_auto_badges: Whether to include auto-generated badges
            
        Returns:
            List of resolved badge objects
        """
        flags = await self.get_player_flags(player_id)
        
        badges = []
        seen_badge_keys = set()
        
        for flag in flags:
            badge = self._resolve_badge_from_flag(flag)
            if badge and badge["badge_key"] not in seen_badge_keys:
                badges.append(badge)
                seen_badge_keys.add(badge["badge_key"])
        
        # Auto-detect "Locked In" if player has distractions but high performance
        if include_auto_badges:
            has_distractions = any(
                b["badge_key"] in ["legal_noise", "distraction"] 
                for b in badges
            )
            has_milestone = any(b["badge_key"] == "milestone" for b in badges)
            
            if has_distractions and has_milestone and "locked_in" not in seen_badge_keys:
                badges.append({
                    "badge_key": "locked_in",
                    "display": "Locked In",
                    "icon": "lock",
                    "color": "#0ea5e9",
                    "description": "Elite focus despite distractions",
                    "severity": 8,
                    "headline": "Performing at elite level despite off-court challenges",
                    "source_flag": "auto_detected"
                })
        
        # Sort by severity (highest first)
        badges.sort(key=lambda x: x.get("severity", 0), reverse=True)
        
        return badges
    
    async def get_player_vision(self, player_id: int) -> Dict[str, Any]:
        """
        Get complete player vision data including stats and badges.
        
        Returns:
            Dict with player info, stats, and active badges
        """
        # Get player from master hub
        player = await self.master_hub.find_one(
            {"nba_player_id": player_id},
            {"_id": 0}
        )
        
        if not player:
            return {"error": f"Player not found: {player_id}"}
        
        # Resolve badges
        badges = await self.resolve_badges(player_id)
        
        # Extract stats
        baseline = player.get("baseline_stats", {})
        pts_stats = baseline.get("PTS", {})
        
        return {
            "player_id": player_id,
            "display_name": player.get("display_name"),
            "team": player.get("team"),
            "headshot_url": player.get("headshot_url"),
            "stats": {
                "ppg": pts_stats.get("season_avg", 0),
                "ppg_l5": pts_stats.get("l5_avg", 0),
                "ppg_l10": pts_stats.get("l10_avg", 0),
                "rpg": baseline.get("REB", {}).get("season_avg", 0),
                "apg": baseline.get("AST", {}).get("season_avg", 0)
            },
            "active_badges": badges,
            "badge_count": len(badges),
            "context_updated_at": datetime.now(timezone.utc).isoformat()
        }
    
    async def add_flag(
        self,
        player_id: int,
        flag_type: str,
        severity: int,
        headline_reference: str = "",
        travel_miles: int = 0,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Add a narrative flag for a player.
        
        Args:
            player_id: NBA player ID
            flag_type: Type of flag (e.g., "legal", "travel", "revenge")
            severity: Severity score 1-10
            headline_reference: Optional headline or source
            travel_miles: Optional travel distance for jet_lag flags
            metadata: Optional additional metadata
            
        Returns:
            Created flag document
        """
        flag_doc = {
            "player_id": player_id,
            "flag_type": flag_type,
            "severity": min(max(severity, 1), 10),  # Clamp 1-10
            "headline_reference": headline_reference,
            "travel_miles": travel_miles,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active": True,
            "source": "manual"
        }
        
        result = await self.context_engine.insert_one(flag_doc)
        flag_doc["_id"] = str(result.inserted_id)
        
        logger.info(f"[BADGE_RESOLVER] Added flag for player {player_id}: {flag_type} (severity: {severity})")
        
        return flag_doc
    
    async def deactivate_flag(self, player_id: int, flag_type: str) -> bool:
        """Deactivate a specific flag for a player."""
        result = await self.context_engine.update_many(
            {"player_id": player_id, "flag_type": flag_type},
            {"$set": {"active": False}}
        )
        return result.modified_count > 0


# Service singleton
_badge_resolver: Optional[BadgeResolverService] = None


def get_badge_resolver(db: AsyncIOMotorDatabase) -> BadgeResolverService:
    """Get or create badge resolver service instance."""
    global _badge_resolver
    if _badge_resolver is None or _badge_resolver.db != db:
        _badge_resolver = BadgeResolverService(db)
    return _badge_resolver
