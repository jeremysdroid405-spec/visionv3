"""
Usage Spike Detector
=====================
Detects vacated usage when primary players are OUT and applies
Vision Score multipliers to the top 2 usage leaders.

Logic:
- When a team's primary rotational player is marked "Out"
- Apply a massive Vision_Score multiplier to the remaining top 2 usage leaders
- Only for offensive props: Points, Assists, PRA (Points+Rebounds+Assists)

Data Source: Existing BDL injury sync (injury_service.py)
"""

import logging
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Configuration
USAGE_BOOST_MULTIPLIER = 1.15    # 15% Vision Score boost for usage spike
MAX_BOOSTED_PLAYERS = 2          # Only top 2 usage leaders get boosted
OFFENSIVE_STAT_TYPES = {"PTS", "AST", "PRA", "FGA"}

# Minimum usage rate to qualify as a "primary" player
PRIMARY_USAGE_THRESHOLD = 20.0   # 20%+ usage rate = primary option


class UsageSpikeDetector:
    """
    Detects vacated usage opportunities from injuries.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._injuries_cache: Dict[str, List[Dict]] = {}  # team -> injuries
        self._usage_rates: Dict[str, Dict] = {}  # player_name -> usage data
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_minutes = 15
    
    async def load_injury_data(self) -> Dict[str, List[Dict]]:
        """
        Load current injury data from MongoDB.
        Groups injuries by team.
        """
        # Check cache
        if self._cache_timestamp:
            age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds() / 60
            if age < self._cache_ttl_minutes and self._injuries_cache:
                return self._injuries_cache
        
        try:
            injuries = await self.db.dg_injuries.find({
                "status": {"$in": ["Out", "Doubtful"]}
            }).to_list(100)
            
            # Group by team
            self._injuries_cache = {}
            for injury in injuries:
                team = injury.get("team", "")
                if team not in self._injuries_cache:
                    self._injuries_cache[team] = []
                self._injuries_cache[team].append(injury)
            
            self._cache_timestamp = datetime.now(timezone.utc)
            
            total_out = sum(len(v) for v in self._injuries_cache.values())
            logger.info(f"[USAGE_SPIKE] Loaded {total_out} OUT/Doubtful players across {len(self._injuries_cache)} teams")
            
            return self._injuries_cache
            
        except Exception as e:
            logger.error(f"[USAGE_SPIKE] Error loading injuries: {e}")
            return {}
    
    async def load_usage_rates(self) -> Dict[str, Dict]:
        """
        Load usage rates from master hub for all active players.
        """
        try:
            # Fetch from nba_master_hub_2026 or daily_insights
            players = await self.db.nba_master_hub_2026.find(
                {"is_active": True},
                {
                    "_id": 0,
                    "display_name": 1,
                    "team": 1,
                    "position": 1,
                    "stats.usage_rate": 1,
                    "stats.ppg": 1,
                    "stats.apg": 1
                }
            ).to_list(500)
            
            for player in players:
                name = player.get("display_name", "")
                usage = player.get("stats", {}).get("usage_rate", 0)
                
                # If no usage_rate, estimate from PPG + APG
                if not usage:
                    ppg = player.get("stats", {}).get("ppg", 0) or 0
                    apg = player.get("stats", {}).get("apg", 0) or 0
                    # Rough estimate: usage ~ (PPG + APG*1.5) / 1.5
                    usage = (ppg + apg * 1.5) / 1.5
                
                self._usage_rates[name.lower()] = {
                    "player_name": name,
                    "team": player.get("team", ""),
                    "position": player.get("position", ""),
                    "usage_rate": float(usage)
                }
            
            logger.info(f"[USAGE_SPIKE] Loaded usage rates for {len(self._usage_rates)} players")
            return self._usage_rates
            
        except Exception as e:
            logger.error(f"[USAGE_SPIKE] Error loading usage rates: {e}")
            return {}
    
    def get_vacated_usage_teams(self) -> Dict[str, List[str]]:
        """
        Get teams with primary players OUT.
        
        Returns:
            {
                "team": ["player1_out", "player2_out", ...]
            }
        """
        vacated = {}
        
        for team, injuries in self._injuries_cache.items():
            for injury in injuries:
                player_name = injury.get("player_name", "")
                player_lower = player_name.lower()
                
                # Check if this is a primary usage player
                usage_data = self._usage_rates.get(player_lower, {})
                usage_rate = usage_data.get("usage_rate", 0)
                
                if usage_rate >= PRIMARY_USAGE_THRESHOLD:
                    if team not in vacated:
                        vacated[team] = []
                    vacated[team].append({
                        "player_name": player_name,
                        "usage_rate": usage_rate,
                        "status": injury.get("status", "Out")
                    })
                    logger.info(f"[USAGE_SPIKE] Primary player OUT: {player_name} ({team}) - {usage_rate:.1f}% usage")
        
        return vacated
    
    def get_top_usage_leaders(self, team: str, exclude: Set[str] = None) -> List[Dict]:
        """
        Get top 2 usage leaders on a team, excluding injured players.
        
        Returns:
            List of {player_name, usage_rate, team} sorted by usage desc
        """
        exclude = exclude or set()
        exclude_lower = {n.lower() for n in exclude}
        
        team_players = []
        for player_lower, data in self._usage_rates.items():
            if data.get("team") == team and player_lower not in exclude_lower:
                team_players.append(data)
        
        # Sort by usage rate descending
        team_players.sort(key=lambda x: x.get("usage_rate", 0), reverse=True)
        
        return team_players[:MAX_BOOSTED_PLAYERS]
    
    async def detect_usage_spikes(self) -> Dict[str, Dict]:
        """
        Detect all usage spike opportunities.
        
        Returns:
            {
                "player_name|team": {
                    "player_name": str,
                    "team": str,
                    "usage_rate": float,
                    "vacated_from": [list of OUT players],
                    "usage_boost": float (multiplier),
                    "vision_score_bonus": int
                }
            }
        """
        # Load data
        await self.load_injury_data()
        await self.load_usage_rates()
        
        vacated_teams = self.get_vacated_usage_teams()
        
        usage_spikes = {}
        
        for team, out_players in vacated_teams.items():
            # Get names of OUT players
            exclude_names = {p["player_name"] for p in out_players}
            
            # Get top 2 remaining usage leaders
            top_leaders = self.get_top_usage_leaders(team, exclude_names)
            
            for leader in top_leaders:
                player_name = leader["player_name"]
                key = f"{player_name}|{team}"
                
                # Calculate bonus based on how much usage is vacated
                total_vacated_usage = sum(p["usage_rate"] for p in out_players)
                
                # Vision score bonus: +5 to +15 based on vacated usage
                # 20%+ usage out = +5, 30%+ = +10, 40%+ = +15
                if total_vacated_usage >= 40:
                    bonus = 15
                elif total_vacated_usage >= 30:
                    bonus = 10
                else:
                    bonus = 5
                
                usage_spikes[key] = {
                    "player_name": player_name,
                    "team": team,
                    "usage_rate": leader["usage_rate"],
                    "vacated_from": [p["player_name"] for p in out_players],
                    "total_vacated_usage": round(total_vacated_usage, 1),
                    "usage_boost": USAGE_BOOST_MULTIPLIER,
                    "vision_score_bonus": bonus
                }
                
                logger.info(f"[USAGE_SPIKE] {player_name} ({team}): +{bonus} vision bonus (vacated: {total_vacated_usage:.1f}%)")
        
        return usage_spikes


def apply_usage_spike_boost(
    props: List[Dict],
    usage_spikes: Dict[str, Dict]
) -> List[Dict]:
    """
    Apply Vision Score boost to props from players with usage spikes.
    
    Only boosts offensive props: PTS, AST, PRA, FGA
    
    Args:
        props: List of candidate props
        usage_spikes: Dict of player_name|team -> spike data
    
    Returns:
        Props with usage spike boost applied
    """
    boosted_count = 0
    
    for prop in props:
        player_name = prop.get("player_name", "")
        team = prop.get("team", "")
        stat_type = (prop.get("stat_type") or prop.get("stat_type_extracted") or "").upper()
        
        # Only boost offensive props
        if stat_type not in OFFENSIVE_STAT_TYPES:
            continue
        
        # Check for usage spike
        key = f"{player_name}|{team}"
        spike_data = usage_spikes.get(key)
        
        if spike_data:
            bonus = spike_data["vision_score_bonus"]
            current_score = prop.get("vision_score", 0)
            
            # Apply bonus
            prop["vision_score"] = min(100, current_score + bonus)
            prop["has_usage_spike"] = True
            prop["usage_spike_data"] = spike_data
            prop["vision_score_breakdown"] = prop.get("vision_score_breakdown", {})
            prop["vision_score_breakdown"]["usage_spike_bonus"] = bonus
            
            boosted_count += 1
            logger.debug(f"[USAGE_SPIKE] +{bonus} to {player_name} {stat_type} (vacated from: {spike_data['vacated_from']})")
    
    if boosted_count > 0:
        logger.info(f"[USAGE_SPIKE] Applied boost to {boosted_count} offensive props")
    
    return props


async def detect_and_apply_usage_spikes(
    db: AsyncIOMotorDatabase,
    props: List[Dict]
) -> Tuple[List[Dict], Dict[str, Dict]]:
    """
    Convenience function to detect usage spikes and apply boosts.
    
    Returns:
        (boosted_props, usage_spikes_data)
    """
    detector = UsageSpikeDetector(db)
    usage_spikes = await detector.detect_usage_spikes()
    
    if usage_spikes:
        props = apply_usage_spike_boost(props, usage_spikes)
    
    return props, usage_spikes
