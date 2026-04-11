"""
MLB Weather Routes - Stadium Weather API
========================================
Endpoints for fetching real-time weather data for MLB stadiums.
"""
from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime, timezone
import logging

from services.mlb_weather_service import (
    get_weather_service, 
    get_stadium_weather, 
    check_wind_tunnel,
    STADIUM_DATA
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["MLB Weather"])


@router.get("/v3/mlb/stadiums")
async def list_stadiums():
    """
    List all MLB stadiums with their data.
    """
    stadiums = []
    for abbr, data in STADIUM_DATA.items():
        stadiums.append({
            "team": abbr,
            "name": data["name"],
            "type": data["type"],
            "latitude": data["lat"],
            "longitude": data["lon"],
            "orientation": data["orientation"]
        })
    
    return {
        "success": True,
        "stadiums": sorted(stadiums, key=lambda x: x["team"]),
        "count": len(stadiums)
    }


@router.get("/v3/mlb/stadium-weather/all")
async def get_all_stadium_weather(
    teams: Optional[str] = Query(None, description="Comma-separated team list (e.g., NYY,LAD,CHC)")
):
    """
    Get weather for all stadiums or specific teams.
    
    Args:
        teams: Optional comma-separated list of team abbreviations
    
    Returns:
        Weather data for all requested stadiums.
    """
    service = get_weather_service()
    
    if teams:
        team_list = [t.strip().upper() for t in teams.split(",")]
    else:
        team_list = list(STADIUM_DATA.keys())
    
    weather_map = await service.get_weather_for_games(team_list)
    
    # Separate into favorable/unfavorable
    favorable = []
    unfavorable = []
    domed = []
    
    for team, weather in weather_map.items():
        if not weather:
            continue
        
        if weather.get("type") == "dome":
            domed.append(weather)
        elif weather.get("is_favorable"):
            favorable.append(weather)
        else:
            unfavorable.append(weather)
    
    return {
        "success": True,
        "total_stadiums": len([w for w in weather_map.values() if w]),
        "favorable_wind": favorable,
        "unfavorable_wind": unfavorable,
        "domed_stadiums": domed,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/mlb/stadium-weather/badge/wind-tunnel/{team}")
async def get_wind_tunnel_badge(team: str):
    """
    Check if Wind Tunnel badge should be applied for a team.
    
    Returns badge data if wind conditions are favorable for fly balls.
    """
    team = team.upper()
    
    if team not in STADIUM_DATA:
        raise HTTPException(status_code=404, detail=f"Unknown team: {team}")
    
    badge = await check_wind_tunnel(team)
    
    return {
        "success": True,
        "team": team,
        "has_badge": badge is not None,
        "badge": badge,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/v3/mlb/stadium-weather/{team}")
async def get_team_weather(team: str):
    """
    Get current weather for a team's stadium.
    
    Args:
        team: Team abbreviation (e.g., NYY, LAD, CHC)
    
    Returns:
        Weather data including wind impact assessment.
    """
    team = team.upper()
    
    if team not in STADIUM_DATA:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown team: {team}. Valid teams: {', '.join(sorted(STADIUM_DATA.keys()))}"
        )
    
    weather = await get_stadium_weather(team)
    
    if not weather:
        raise HTTPException(
            status_code=503,
            detail=f"Weather data unavailable for {team}"
        )
    
    return {
        "success": True,
        "weather": weather,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
