"""
MLB Weather Service
===================
Fetches real-time weather data for MLB stadiums and determines
wind impact on gameplay (Wind Tunnel badge).

Uses Open-Meteo API (free, no key required).

Stadium Orientation:
- Most stadiums face NE to minimize sun glare for batters
- Wind "blowing out" = wind from home plate toward center field
- Each stadium has different outfield orientation
"""

import asyncio
import httpx
import logging
from typing import Dict, Optional, Any, Tuple
from datetime import datetime, timezone
from functools import lru_cache

logger = logging.getLogger(__name__)

# Open-Meteo API (free, no key needed)
WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

# Stadium coordinates and outfield orientation (degrees)
# Orientation = direction from home plate to center field
# Wind blowing OUT = wind direction opposite to orientation (within 90 degrees)
STADIUM_DATA = {
    "ARI": {"name": "Chase Field", "lat": 33.4455, "lon": -112.0667, "orientation": 60, "type": "dome"},
    "ATL": {"name": "Truist Park", "lat": 33.8908, "lon": -84.4678, "orientation": 45, "type": "outdoor"},
    "BAL": {"name": "Camden Yards", "lat": 39.2838, "lon": -76.6216, "orientation": 65, "type": "outdoor"},
    "BOS": {"name": "Fenway Park", "lat": 42.3467, "lon": -71.0972, "orientation": 67, "type": "outdoor"},
    "CHC": {"name": "Wrigley Field", "lat": 41.9484, "lon": -87.6553, "orientation": 43, "type": "outdoor"},  # Famous for wind
    "CHW": {"name": "Guaranteed Rate Field", "lat": 41.8299, "lon": -87.6338, "orientation": 70, "type": "outdoor"},
    "CIN": {"name": "Great American Ball Park", "lat": 39.0974, "lon": -84.5082, "orientation": 95, "type": "outdoor"},
    "CLE": {"name": "Progressive Field", "lat": 41.4962, "lon": -81.6852, "orientation": 45, "type": "outdoor"},
    "COL": {"name": "Coors Field", "lat": 39.7559, "lon": -104.9942, "orientation": 60, "type": "outdoor"},  # High altitude
    "DET": {"name": "Comerica Park", "lat": 42.3390, "lon": -83.0485, "orientation": 45, "type": "outdoor"},
    "HOU": {"name": "Minute Maid Park", "lat": 29.7573, "lon": -95.3555, "orientation": 70, "type": "retractable"},
    "KC": {"name": "Kauffman Stadium", "lat": 39.0517, "lon": -94.4803, "orientation": 75, "type": "outdoor"},
    "LAA": {"name": "Angel Stadium", "lat": 33.8003, "lon": -117.8827, "orientation": 50, "type": "outdoor"},
    "LAD": {"name": "Dodger Stadium", "lat": 34.0739, "lon": -118.2400, "orientation": 0, "type": "outdoor"},
    "MIA": {"name": "LoanDepot Park", "lat": 25.7781, "lon": -80.2197, "orientation": 35, "type": "retractable"},
    "MIL": {"name": "American Family Field", "lat": 43.0280, "lon": -87.9712, "orientation": 65, "type": "retractable"},
    "MIN": {"name": "Target Field", "lat": 44.9817, "lon": -93.2778, "orientation": 40, "type": "outdoor"},
    "NYM": {"name": "Citi Field", "lat": 40.7571, "lon": -73.8458, "orientation": 60, "type": "outdoor"},
    "NYY": {"name": "Yankee Stadium", "lat": 40.8296, "lon": -73.9262, "orientation": 87, "type": "outdoor"},
    "OAK": {"name": "Oakland Coliseum", "lat": 37.7516, "lon": -122.2005, "orientation": 55, "type": "outdoor"},
    "PHI": {"name": "Citizens Bank Park", "lat": 39.9061, "lon": -75.1665, "orientation": 65, "type": "outdoor"},
    "PIT": {"name": "PNC Park", "lat": 40.4469, "lon": -80.0057, "orientation": 50, "type": "outdoor"},
    "SD": {"name": "Petco Park", "lat": 32.7076, "lon": -117.1570, "orientation": 68, "type": "outdoor"},
    "SEA": {"name": "T-Mobile Park", "lat": 47.5914, "lon": -122.3325, "orientation": 55, "type": "retractable"},
    "SF": {"name": "Oracle Park", "lat": 37.7786, "lon": -122.3893, "orientation": 65, "type": "outdoor"},  # McCovey Cove
    "STL": {"name": "Busch Stadium", "lat": 38.6226, "lon": -90.1928, "orientation": 45, "type": "outdoor"},
    "TB": {"name": "Tropicana Field", "lat": 27.7682, "lon": -82.6534, "orientation": 45, "type": "dome"},
    "TEX": {"name": "Globe Life Field", "lat": 32.7473, "lon": -97.0832, "orientation": 60, "type": "retractable"},
    "TOR": {"name": "Rogers Centre", "lat": 43.6414, "lon": -79.3894, "orientation": 75, "type": "retractable"},
    "WSH": {"name": "Nationals Park", "lat": 38.8730, "lon": -77.0074, "orientation": 95, "type": "outdoor"},
}

# Wind impact thresholds
WIND_TUNNEL_THRESHOLD = 10  # mph for significant wind boost
WIND_STRONG_THRESHOLD = 15  # mph for major wind factor
WIND_EXTREME_THRESHOLD = 20 # mph for extreme conditions


def calculate_wind_impact(wind_direction: float, wind_speed: float, stadium_orientation: float) -> Dict[str, Any]:
    """
    Calculate if wind is blowing out (favorable) or in (unfavorable).
    
    Args:
        wind_direction: Wind coming FROM this direction (0-360 degrees)
        wind_speed: Wind speed in mph
        stadium_orientation: Direction from home plate to CF (0-360 degrees)
    
    Returns:
        Dict with wind impact assessment
    """
    # Wind blowing OUT = wind coming from behind home plate
    # This means wind direction should be OPPOSITE to stadium orientation
    # (wind blows FROM direction, stadium faces TO direction)
    
    # Calculate the angle difference
    # Wind blowing out: wind coming from HP side (orientation - 180)
    out_direction = (stadium_orientation + 180) % 360
    
    # Calculate angular difference
    diff = abs(wind_direction - out_direction)
    if diff > 180:
        diff = 360 - diff
    
    # Determine wind effect
    if diff <= 45:
        # Wind is blowing OUT (toward outfield)
        effect = "out"
        multiplier = 1.0 - (diff / 45) * 0.3  # 1.0 at direct, 0.7 at 45 degrees
    elif diff >= 135:
        # Wind is blowing IN (toward home plate)
        effect = "in"
        multiplier = -(1.0 - ((180 - diff) / 45) * 0.3)
    else:
        # Crosswind
        effect = "cross"
        multiplier = 0.0
    
    # Calculate impact score
    impact_score = wind_speed * multiplier if effect != "cross" else 0
    
    return {
        "effect": effect,
        "direction_deg": wind_direction,
        "stadium_orientation": stadium_orientation,
        "angle_diff": diff,
        "multiplier": round(multiplier, 2),
        "impact_score": round(impact_score, 1),
        "is_favorable": effect == "out" and wind_speed >= WIND_TUNNEL_THRESHOLD,
        "is_strong": wind_speed >= WIND_STRONG_THRESHOLD,
        "description": _get_wind_description(effect, wind_speed)
    }


def _get_wind_description(effect: str, speed: float) -> str:
    """Get human-readable wind description."""
    if speed < 5:
        return "Calm conditions"
    
    intensity = "Light" if speed < 10 else "Moderate" if speed < 15 else "Strong" if speed < 20 else "Very strong"
    
    if effect == "out":
        return f"{intensity} wind blowing OUT to center ({speed:.0f} mph) - favors fly balls"
    elif effect == "in":
        return f"{intensity} wind blowing IN ({speed:.0f} mph) - suppresses fly balls"
    else:
        return f"{intensity} crosswind ({speed:.0f} mph) - unpredictable carry"


class MLBWeatherService:
    """MLB Weather Service for real-time stadium weather."""
    
    def __init__(self):
        self._cache: Dict[str, Tuple[datetime, Dict]] = {}
        self._cache_duration = 900  # 15 minutes
    
    def _get_cached(self, team: str) -> Optional[Dict]:
        """Get cached weather if still fresh."""
        if team in self._cache:
            cached_time, data = self._cache[team]
            age = (datetime.now(timezone.utc) - cached_time).total_seconds()
            if age < self._cache_duration:
                return data
        return None
    
    async def get_weather(self, team: str) -> Optional[Dict[str, Any]]:
        """
        Fetch weather for a team's stadium.
        
        Args:
            team: Team abbreviation (e.g., "NYY", "LAD")
        
        Returns:
            Weather data dict or None
        """
        team = team.upper()
        
        # Check cache
        cached = self._get_cached(team)
        if cached:
            logger.debug(f"[Weather] Using cached weather for {team}")
            return cached
        
        # Get stadium data
        stadium = STADIUM_DATA.get(team)
        if not stadium:
            logger.warning(f"[Weather] Unknown team: {team}")
            return None
        
        # Domed stadiums don't need weather
        if stadium["type"] == "dome":
            return {
                "team": team,
                "stadium": stadium["name"],
                "type": "dome",
                "temperature": 72,  # Standard dome temp
                "wind_speed": 0,
                "wind_direction": 0,
                "wind_effect": "none",
                "is_favorable": False,
                "description": "Indoor stadium - no weather impact"
            }
        
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "latitude": stadium["lat"],
                    "longitude": stadium["lon"],
                    "current_weather": True,
                    "temperature_unit": "fahrenheit",
                    "windspeed_unit": "mph",
                    "timezone": "auto"
                }
                
                resp = await client.get(WEATHER_API_URL, params=params, timeout=10)
                
                if resp.status_code != 200:
                    logger.warning(f"[Weather] API returned {resp.status_code} for {team}")
                    return None
                
                data = resp.json()
                current = data.get("current_weather", {})
                
                wind_speed = current.get("windspeed", 0)
                wind_direction = current.get("winddirection", 0)
                temperature = current.get("temperature", 70)
                
                # Calculate wind impact
                wind_impact = calculate_wind_impact(
                    wind_direction,
                    wind_speed,
                    stadium["orientation"]
                )
                
                result = {
                    "team": team,
                    "stadium": stadium["name"],
                    "type": stadium["type"],
                    "temperature": round(temperature),
                    "wind_speed": round(wind_speed, 1),
                    "wind_direction": round(wind_direction),
                    "wind_effect": wind_impact["effect"],
                    "wind_impact_score": wind_impact["impact_score"],
                    "is_favorable": wind_impact["is_favorable"],
                    "is_strong": wind_impact["is_strong"],
                    "description": wind_impact["description"],
                    "fetched_at": datetime.now(timezone.utc).isoformat()
                }
                
                # Cache result
                self._cache[team] = (datetime.now(timezone.utc), result)
                
                logger.info(f"[Weather] {team}: {result['description']}")
                return result
                
        except asyncio.TimeoutError:
            logger.error(f"[Weather] Timeout fetching weather for {team}")
        except Exception as e:
            logger.error(f"[Weather] Error fetching weather for {team}: {e}")
        
        return None
    
    async def get_wind_tunnel_badge(self, team: str) -> Optional[Dict[str, Any]]:
        """
        Check if Wind Tunnel badge should be applied.
        
        Returns badge data if conditions are favorable, None otherwise.
        """
        weather = await self.get_weather(team)
        
        if not weather:
            return None
        
        if weather.get("type") == "dome":
            return None
        
        if weather.get("is_favorable"):
            return {
                "id": "wind_tunnel",
                "name": "Wind Tunnel",
                "emoji": "🌪️",
                "description": weather["description"],
                "is_positive": True,
                "metrics": {
                    "wind_speed": weather["wind_speed"],
                    "wind_direction": weather["wind_direction"],
                    "wind_effect": weather["wind_effect"],
                    "impact_score": weather["wind_impact_score"],
                    "stadium": weather["stadium"]
                }
            }
        
        return None
    
    async def get_weather_for_games(self, teams: list) -> Dict[str, Dict]:
        """
        Fetch weather for multiple teams concurrently.
        
        Args:
            teams: List of team abbreviations
        
        Returns:
            Dict mapping team -> weather data
        """
        tasks = [self.get_weather(team) for team in teams]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        weather_map = {}
        for team, result in zip(teams, results):
            if isinstance(result, Exception):
                logger.error(f"[Weather] Exception for {team}: {result}")
                weather_map[team] = None
            else:
                weather_map[team] = result
        
        return weather_map


# Singleton
_weather_service: Optional[MLBWeatherService] = None


def get_weather_service() -> MLBWeatherService:
    """Get or create Weather Service instance."""
    global _weather_service
    if _weather_service is None:
        _weather_service = MLBWeatherService()
    return _weather_service


# Convenience functions
async def get_stadium_weather(team: str) -> Optional[Dict]:
    """Get weather for a team's stadium."""
    service = get_weather_service()
    return await service.get_weather(team)


async def check_wind_tunnel(team: str) -> Optional[Dict]:
    """Check if Wind Tunnel badge applies for a team."""
    service = get_weather_service()
    return await service.get_wind_tunnel_badge(team)
