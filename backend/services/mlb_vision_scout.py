"""
MLB Vision Scout Service
=========================
Evaluates Statcast and environmental data to attach scout badges to props.

Badge Categories:
- Batters: Contact King, Barrel Master, High-Heat Trap
- Pitchers: Workhorse, Zone Painter, Whiff Wizard, Short Leash
- Environment: Wind Tunnel, Travel Lag
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# =============================================================================
# BADGE DEFINITIONS
# =============================================================================

BATTER_BADGES = {
    "contact_king": {
        "id": "contact_king",
        "emoji": "🟢",
        "name": "Contact King",
        "description": "Elite bat-to-ball skills (Whiff Rate < 15%)",
        "criteria": {"whiff_rate_max": 0.15}
    },
    "barrel_master": {
        "id": "barrel_master",
        "emoji": "🔥",
        "name": "Barrel Master",
        "description": "Consistent hard contact (Barrel Rate > 15%)",
        "criteria": {"barrel_rate_min": 0.15}
    },
    "high_heat_trap": {
        "id": "high_heat_trap",
        "emoji": "📉",
        "name": "High-Heat Trap",
        "description": "WARNING: Struggles vs elite velocity (K% > 28% vs 97mph+)",
        "criteria": {"k_rate_vs_velo_min": 0.28},
        "is_negative": True
    }
}

PITCHER_BADGES = {
    "workhorse": {
        "id": "workhorse",
        "emoji": "🔵",
        "name": "Workhorse",
        "description": "Deep game pitcher (Outs line < 17.5 & L5 IP > 6.0)",
        "criteria": {"outs_line_max": 17.5, "l5_ip_min": 6.0}
    },
    "zone_painter": {
        "id": "zone_painter",
        "emoji": "🎯",
        "name": "Zone Painter",
        "description": "Elite command (Walk Rate < 5%)",
        "criteria": {"walk_rate_max": 0.05}
    },
    "whiff_wizard": {
        "id": "whiff_wizard",
        "emoji": "💨",
        "name": "Whiff Wizard",
        "description": "Strikeout machine (K% > 31%)",
        "criteria": {"k_rate_min": 0.31}
    },
    "short_leash": {
        "id": "short_leash",
        "emoji": "⚠️",
        "name": "Short Leash",
        "description": "WARNING: Early hook risk (Failed 5.0 IP in 3 of L4)",
        "criteria": {"failed_5ip_count": 3},
        "is_negative": True
    }
}

ENVIRONMENT_BADGES = {
    "wind_tunnel": {
        "id": "wind_tunnel",
        "emoji": "🌪️",
        "name": "Wind Tunnel",
        "description": "Favorable wind conditions (Wind Out > 12mph)",
        "criteria": {"wind_out_min": 12}
    },
    "travel_lag": {
        "id": "travel_lag",
        "emoji": "✈️",
        "name": "Travel Lag",
        "description": "WARNING: First game post-cross-country flight",
        "criteria": {"is_first_after_travel": True},
        "is_negative": True
    }
}

# Park factors for wind assessment
WIND_FAVORABLE_PARKS = {
    "coors_field": 1.3,
    "great_american_ball_park": 1.15,
    "citizens_bank_park": 1.1,
    "fenway_park": 1.08,
    "wrigley_field": 1.12,  # When wind is blowing out
}


class MLBVisionScout:
    """
    MLB Vision Scout - Badge Evaluation System.
    
    Analyzes Statcast and environmental data to assign
    scout badges to props.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self._player_cache: Dict[str, Dict] = {}
    
    async def _load_player_data(self, player_name: str) -> Optional[Dict]:
        """Load player data from master hub."""
        if player_name.lower() in self._player_cache:
            return self._player_cache[player_name.lower()]
        
        master_hub = self.db["mlb_master_hub_2026"]
        player = await master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if player:
            self._player_cache[player_name.lower()] = player
        
        return player
    
    def _calculate_whiff_rate(self, player: Dict) -> Optional[float]:
        """Calculate whiff rate from game logs."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 10:
            return None
        
        total_k = 0
        total_ab = 0
        
        for game in game_logs[:20]:
            k = game.get("strikeouts") or 0
            ab = game.get("at_bats") or 0
            total_k += k
            total_ab += ab
        
        if total_ab < 40:
            return None
        
        # Whiff rate ≈ K% * 0.45 (proxy from K rate)
        k_rate = total_k / total_ab
        whiff_rate = k_rate * 0.45
        
        return round(whiff_rate, 3)
    
    def _calculate_barrel_rate(self, player: Dict) -> Optional[float]:
        """Estimate barrel rate from XBH and HR."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 10:
            return None
        
        total_xbh = 0
        total_ab = 0
        total_hr = 0
        
        for game in game_logs[:25]:
            hits = game.get("hits") or 0
            doubles = game.get("doubles") or 0
            triples = game.get("triples") or 0
            hr = game.get("home_runs") or 0
            ab = game.get("at_bats") or 0
            
            xbh = doubles + triples + hr
            total_xbh += xbh
            total_hr += hr
            total_ab += ab
        
        if total_ab < 50:
            return None
        
        # Barrel rate proxy: (XBH rate * 1.5) + (HR rate * 3)
        xbh_rate = total_xbh / total_ab
        hr_rate = total_hr / total_ab
        barrel_rate = (xbh_rate * 1.5) + (hr_rate * 3)
        
        return round(min(barrel_rate, 0.25), 3)  # Cap at 25%
    
    def _calculate_k_rate(self, player: Dict, is_pitcher: bool = False) -> Optional[float]:
        """Calculate strikeout rate."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 5:
            return None
        
        if is_pitcher:
            total_k = 0
            total_bf = 0  # Batters faced proxy
            
            for game in game_logs[:15]:
                k = game.get("pitcher_strikeouts") or 0
                ip = game.get("innings_pitched") or 0
                # BF ≈ IP * 4.3 (average batters per inning)
                bf = ip * 4.3
                total_k += k
                total_bf += bf
            
            if total_bf < 30:
                return None
            
            return round(total_k / total_bf, 3)
        else:
            total_k = 0
            total_ab = 0
            
            for game in game_logs[:20]:
                k = game.get("strikeouts") or 0
                ab = game.get("at_bats") or 0
                total_k += k
                total_ab += ab
            
            if total_ab < 40:
                return None
            
            return round(total_k / total_ab, 3)
    
    def _calculate_walk_rate(self, player: Dict) -> Optional[float]:
        """Calculate walk rate for pitchers."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 5:
            return None
        
        total_walks = 0
        total_bf = 0
        
        for game in game_logs[:15]:
            walks = game.get("pitcher_walks") or 0
            ip = game.get("innings_pitched") or 0
            bf = ip * 4.3
            total_walks += walks
            total_bf += bf
        
        if total_bf < 30:
            return None
        
        return round(total_walks / total_bf, 3)
    
    def _check_workhorse(self, player: Dict, prop: Dict) -> bool:
        """Check if pitcher qualifies as Workhorse."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 5:
            return False
        
        # Check outs line < 17.5
        line = prop.get("line", 0)
        stat_type = prop.get("stat_type", "").lower()
        
        if "out" not in stat_type and "inning" not in stat_type:
            # Check if prop line is for strikeouts - not applicable
            if line > 17.5:
                return False
        
        # Check L5 IP > 6.0
        l5_ip = []
        for game in game_logs[:5]:
            ip = game.get("innings_pitched")
            if ip is not None:
                l5_ip.append(ip)
        
        if len(l5_ip) < 3:
            return False
        
        avg_ip = sum(l5_ip) / len(l5_ip)
        return avg_ip >= 6.0
    
    def _check_short_leash(self, player: Dict) -> bool:
        """Check if pitcher has Short Leash warning."""
        game_logs = player.get("bdl_game_logs", [])
        if len(game_logs) < 4:
            return False
        
        failed_5ip = 0
        for game in game_logs[:4]:
            ip = game.get("innings_pitched") or 0
            if ip < 5.0:
                failed_5ip += 1
        
        return failed_5ip >= 3
    
    async def evaluate_batter_badges(
        self,
        player_name: str,
        prop: Dict
    ) -> List[Dict]:
        """Evaluate all batter badges for a player."""
        badges = []
        
        player = await self._load_player_data(player_name)
        if not player:
            return badges
        
        # Contact King - Whiff Rate < 15%
        whiff_rate = self._calculate_whiff_rate(player)
        if whiff_rate is not None and whiff_rate < 0.15:
            badge = {**BATTER_BADGES["contact_king"]}
            badge["metrics"] = {"whiff_rate": whiff_rate}
            badges.append(badge)
        
        # Barrel Master - Barrel Rate > 15%
        barrel_rate = self._calculate_barrel_rate(player)
        if barrel_rate is not None and barrel_rate > 0.15:
            badge = {**BATTER_BADGES["barrel_master"]}
            badge["metrics"] = {"barrel_rate": barrel_rate}
            badges.append(badge)
        
        # High-Heat Trap - K% > 28% (warning badge)
        k_rate = self._calculate_k_rate(player, is_pitcher=False)
        if k_rate is not None and k_rate > 0.28:
            badge = {**BATTER_BADGES["high_heat_trap"]}
            badge["metrics"] = {"k_rate": k_rate}
            badges.append(badge)
        
        return badges
    
    async def evaluate_pitcher_badges(
        self,
        player_name: str,
        prop: Dict
    ) -> List[Dict]:
        """Evaluate all pitcher badges for a player."""
        badges = []
        
        player = await self._load_player_data(player_name)
        if not player:
            return badges
        
        # Workhorse - Deep game pitcher
        if self._check_workhorse(player, prop):
            badge = {**PITCHER_BADGES["workhorse"]}
            game_logs = player.get("bdl_game_logs", [])
            l5_ip = [g.get("innings_pitched") or 0 for g in game_logs[:5]]
            badge["metrics"] = {"l5_avg_ip": round(sum(l5_ip) / len(l5_ip), 1) if l5_ip else 0}
            badges.append(badge)
        
        # Zone Painter - Walk Rate < 5%
        walk_rate = self._calculate_walk_rate(player)
        if walk_rate is not None and walk_rate < 0.05:
            badge = {**PITCHER_BADGES["zone_painter"]}
            badge["metrics"] = {"walk_rate": walk_rate}
            badges.append(badge)
        
        # Whiff Wizard - K% > 31%
        k_rate = self._calculate_k_rate(player, is_pitcher=True)
        if k_rate is not None and k_rate > 0.31:
            badge = {**PITCHER_BADGES["whiff_wizard"]}
            badge["metrics"] = {"k_rate": k_rate}
            badges.append(badge)
        
        # Short Leash - Warning badge
        if self._check_short_leash(player):
            badge = {**PITCHER_BADGES["short_leash"]}
            badges.append(badge)
        
        return badges
    
    def evaluate_environment_badges(
        self,
        prop: Dict,
        weather: Optional[Dict] = None,
        is_travel_game: bool = False
    ) -> List[Dict]:
        """Evaluate environment badges."""
        badges = []
        
        # Wind Tunnel - Wind Out > 12mph
        if weather:
            wind_speed = weather.get("wind_speed", 0)
            wind_direction = weather.get("wind_direction", "")
            
            # Check if wind is blowing out
            if wind_speed >= 12 and wind_direction.lower() in ["out", "outfield", "cf", "center"]:
                badge = {**ENVIRONMENT_BADGES["wind_tunnel"]}
                badge["metrics"] = {"wind_speed": wind_speed, "direction": wind_direction}
                badges.append(badge)
        
        # Travel Lag - First game after cross-country flight
        if is_travel_game:
            badge = {**ENVIRONMENT_BADGES["travel_lag"]}
            badges.append(badge)
        
        return badges
    
    async def evaluate_all_badges(
        self,
        player_name: str,
        prop: Dict,
        is_pitcher: bool = False,
        weather: Optional[Dict] = None,
        is_travel_game: bool = False
    ) -> List[Dict]:
        """
        Evaluate all applicable badges for a prop.
        
        Returns:
            List of earned badges
        """
        badges = []
        
        # Player-specific badges
        if is_pitcher or "pitcher" in prop.get("stat_type", "").lower():
            badges.extend(await self.evaluate_pitcher_badges(player_name, prop))
        else:
            badges.extend(await self.evaluate_batter_badges(player_name, prop))
        
        # Environment badges
        badges.extend(self.evaluate_environment_badges(prop, weather, is_travel_game))
        
        return badges


# Singleton
_vision_scout: Optional[MLBVisionScout] = None


def get_vision_scout(db: AsyncIOMotorDatabase) -> MLBVisionScout:
    """Get or create Vision Scout instance."""
    global _vision_scout
    if _vision_scout is None:
        _vision_scout = MLBVisionScout(db)
    return _vision_scout
