"""
MLB Four-Gate System
====================
The complete pipeline for MLB prop analysis.

GATE 1 - THE MATH: VK Linear Regression (5-Year History)
GATE 2 - THE MARKET: Sharp Book (Pinnacle) + DK Alt Lines  
GATE 3 - THE SCOUT: Vision Intel (Weather, Park Factor, Statcast, BvP)
GATE 4 - THE BRAIN: Oracle Adversarial Verdict (Bull vs Bear)

TRAP DETECTOR:
- Flags props where weather/matchup invalidates VK regression
- Outdoor venue + Wind > 15mph + HR prop = TRAP
- Pitcher velocity drop > 2mph = TRAP
- Batter vs specific pitch type < .150 avg = TRAP
"""

import os
import logging
import httpx
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# =============================================================================
# PARK FACTORS (2024-2026 averages)
# =============================================================================
# Park Factor > 1.0 = Hitter friendly, < 1.0 = Pitcher friendly
PARK_FACTORS = {
    # Hitter-friendly parks
    "COL": {"name": "Coors Field", "factor": 1.38, "hr_factor": 1.42, "type": "outdoor", "altitude": 5280},
    "CIN": {"name": "Great American Ball Park", "factor": 1.12, "hr_factor": 1.18, "type": "outdoor", "altitude": 550},
    "TEX": {"name": "Globe Life Field", "factor": 1.08, "hr_factor": 1.10, "type": "retractable", "altitude": 500},
    "PHI": {"name": "Citizens Bank Park", "factor": 1.07, "hr_factor": 1.12, "type": "outdoor", "altitude": 39},
    "BOS": {"name": "Fenway Park", "factor": 1.06, "hr_factor": 0.95, "type": "outdoor", "altitude": 20},
    "CHC": {"name": "Wrigley Field", "factor": 1.05, "hr_factor": 1.08, "type": "outdoor", "altitude": 600},
    "ARI": {"name": "Chase Field", "factor": 1.04, "hr_factor": 1.06, "type": "retractable", "altitude": 1082},
    "NYY": {"name": "Yankee Stadium", "factor": 1.04, "hr_factor": 1.15, "type": "outdoor", "altitude": 55},
    "MIL": {"name": "American Family Field", "factor": 1.03, "hr_factor": 1.08, "type": "retractable", "altitude": 635},
    "ATL": {"name": "Truist Park", "factor": 1.02, "hr_factor": 1.05, "type": "outdoor", "altitude": 1050},
    
    # Neutral parks
    "BAL": {"name": "Camden Yards", "factor": 1.01, "hr_factor": 1.03, "type": "outdoor", "altitude": 33},
    "MIN": {"name": "Target Field", "factor": 1.01, "hr_factor": 1.02, "type": "outdoor", "altitude": 841},
    "DET": {"name": "Comerica Park", "factor": 1.00, "hr_factor": 0.92, "type": "outdoor", "altitude": 600},
    "CLE": {"name": "Progressive Field", "factor": 1.00, "hr_factor": 0.98, "type": "outdoor", "altitude": 660},
    "CHW": {"name": "Guaranteed Rate Field", "factor": 1.00, "hr_factor": 1.05, "type": "outdoor", "altitude": 595},
    "TOR": {"name": "Rogers Centre", "factor": 1.00, "hr_factor": 1.02, "type": "retractable", "altitude": 269},
    "HOU": {"name": "Minute Maid Park", "factor": 1.00, "hr_factor": 1.04, "type": "retractable", "altitude": 50},
    "WSH": {"name": "Nationals Park", "factor": 0.99, "hr_factor": 0.98, "type": "outdoor", "altitude": 25},
    "LAA": {"name": "Angel Stadium", "factor": 0.99, "hr_factor": 0.96, "type": "outdoor", "altitude": 160},
    
    # Pitcher-friendly parks
    "STL": {"name": "Busch Stadium", "factor": 0.98, "hr_factor": 0.95, "type": "outdoor", "altitude": 465},
    "KC": {"name": "Kauffman Stadium", "factor": 0.97, "hr_factor": 0.88, "type": "outdoor", "altitude": 820},
    "PIT": {"name": "PNC Park", "factor": 0.96, "hr_factor": 0.92, "type": "outdoor", "altitude": 730},
    "NYM": {"name": "Citi Field", "factor": 0.95, "hr_factor": 0.90, "type": "outdoor", "altitude": 20},
    "TB": {"name": "Tropicana Field", "factor": 0.94, "hr_factor": 0.88, "type": "dome", "altitude": 43},
    "SEA": {"name": "T-Mobile Park", "factor": 0.93, "hr_factor": 0.85, "type": "retractable", "altitude": 21},
    "SF": {"name": "Oracle Park", "factor": 0.92, "hr_factor": 0.80, "type": "outdoor", "altitude": 3},
    "LAD": {"name": "Dodger Stadium", "factor": 0.92, "hr_factor": 0.90, "type": "outdoor", "altitude": 515},
    "MIA": {"name": "LoanDepot Park", "factor": 0.91, "hr_factor": 0.86, "type": "retractable", "altitude": 10},
    "SD": {"name": "Petco Park", "factor": 0.90, "hr_factor": 0.82, "type": "outdoor", "altitude": 17},
    "OAK": {"name": "Oakland Coliseum", "factor": 0.89, "hr_factor": 0.78, "type": "outdoor", "altitude": 20},
}

# Default for unknown teams
DEFAULT_PARK = {"name": "Unknown", "factor": 1.00, "hr_factor": 1.00, "type": "outdoor", "altitude": 500}

# =============================================================================
# WEATHER THRESHOLDS FOR TRAP DETECTION
# =============================================================================
WEATHER_TRAPS = {
    "wind_hr_trap": 15,       # Wind > 15mph = HR trap (direction dependent)
    "wind_tb_trap": 20,       # Wind > 20mph = TB trap  
    "cold_temp_trap": 45,     # Temp < 45°F = Offense suppressed
    "rain_chance_trap": 40,   # Rain > 40% = Game delay/PPD risk
}

# =============================================================================
# STATCAST THRESHOLDS
# =============================================================================
STATCAST_THRESHOLDS = {
    "elite_barrel_pct": 12.0,      # Top tier barrel rate
    "good_barrel_pct": 8.0,        # Above average
    "elite_hard_hit_pct": 45.0,    # Elite hard hit rate
    "good_hard_hit_pct": 38.0,     # Above average
    "velocity_drop_trap": 2.0,     # Pitcher velo drop > 2mph = TRAP
    "spin_drop_trap": 150,         # Spin rate drop > 150rpm = TRAP
    "weak_split_avg": 0.150,       # Batter avg < .150 vs pitch type = TRAP
}


class MLBFourGateSystem:
    """
    MLB Four-Gate Prop Analysis System.
    
    Processes props through 4 gates:
    1. The Math (VK Regression)
    2. The Market (Sharp/DK Lines)
    3. The Scout (Weather, Park, Statcast)
    4. The Brain (Oracle Verdict)
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.master_hub = db.mlb_master_hub_2026
        self.historical_logs = db.mlb_historical_logs
        self.live_props = db.mlb_live_props
        self.cached_board = db.mlb_cached_board
        
        # Weather API (Open-Meteo - free, no key required)
        self.weather_api = "https://api.open-meteo.com/v1/forecast"
        
        # Venue coordinates for weather lookup
        self.venue_coords = {
            "COL": (39.7559, -104.9942),  # Coors Field
            "CIN": (39.0974, -84.5082),   # Great American
            "TEX": (32.7473, -97.0832),   # Globe Life
            "PHI": (39.9061, -75.1665),   # Citizens Bank
            "BOS": (42.3467, -71.0972),   # Fenway
            "CHC": (41.9484, -87.6553),   # Wrigley
            "ARI": (33.4455, -112.0667),  # Chase Field
            "NYY": (40.8296, -73.9262),   # Yankee Stadium
            "MIL": (43.0280, -87.9712),   # American Family
            "ATL": (33.8908, -84.4678),   # Truist Park
            "BAL": (39.2838, -76.6216),   # Camden Yards
            "MIN": (44.9817, -93.2778),   # Target Field
            "DET": (42.3390, -83.0485),   # Comerica
            "CLE": (41.4962, -81.6852),   # Progressive
            "CHW": (41.8299, -87.6338),   # Guaranteed Rate
            "TOR": (43.6414, -79.3894),   # Rogers Centre
            "HOU": (29.7573, -95.3555),   # Minute Maid
            "WSH": (38.8730, -77.0074),   # Nationals Park
            "LAA": (33.8003, -117.8827),  # Angel Stadium
            "STL": (38.6226, -90.1928),   # Busch Stadium
            "KC": (39.0517, -94.4803),    # Kauffman
            "PIT": (40.4469, -80.0057),   # PNC Park
            "NYM": (40.7571, -73.8458),   # Citi Field
            "TB": (27.7682, -82.6534),    # Tropicana
            "SEA": (47.5914, -122.3325),  # T-Mobile
            "SF": (37.7786, -122.3893),   # Oracle Park
            "LAD": (34.0739, -118.2400),  # Dodger Stadium
            "MIA": (25.7781, -80.2197),   # LoanDepot
            "SD": (32.7076, -117.1570),   # Petco Park
            "OAK": (37.7516, -122.2005),  # Oakland Coliseum
        }
    
    # =========================================================================
    # GATE 1: THE MATH (VK Regression)
    # =========================================================================
    
    async def get_vk_data(self, player_name: str, stat_type: str) -> Dict[str, Any]:
        """
        Get VK regression data for a player/stat.
        
        Returns:
            Dict with projected_value, edge_pct, r_squared, probability
        """
        # Check VK projections collection
        vk_collection = self.db.mlb_vk_projections
        
        projection = await vk_collection.find_one(
            {"player_name": player_name, "stat_type": stat_type},
            {"_id": 0}
        )
        
        if projection:
            return {
                "gate": "MATH",
                "source": "VK_Regression",
                "projected_value": projection.get("projected_value"),
                "edge_pct": projection.get("edge_pct"),
                "r_squared": projection.get("r_squared"),
                "probability": projection.get("probability"),
                "sample_size": projection.get("sample_size"),
                "pass": projection.get("r_squared", 0) >= 0.60 and projection.get("edge_pct", 0) >= 5.0
            }
        
        return {
            "gate": "MATH",
            "source": "VK_Regression",
            "projected_value": None,
            "edge_pct": None,
            "r_squared": None,
            "probability": None,
            "pass": False,
            "reason": "No VK projection available"
        }
    
    # =========================================================================
    # GATE 2: THE MARKET (Sharp + DK Lines)
    # =========================================================================
    
    def get_market_data(self, prop: Dict) -> Dict[str, Any]:
        """
        Analyze market data from multiple books.
        
        Returns:
            Dict with pp_line, dk_line, sharp_line, edges, and market verdict
        """
        pp_line = prop.get("pp_line")
        pp_odds = prop.get("pp_odds")
        dk_line = prop.get("dk_line")
        dk_odds = prop.get("dk_odds")
        sharp_line = prop.get("sharp_line")
        sharp_odds = prop.get("sharp_odds")
        
        result = {
            "gate": "MARKET",
            "source": "Multi-Book",
            "pp_line": pp_line,
            "pp_odds": pp_odds,
            "dk_line": dk_line,
            "dk_odds": dk_odds,
            "sharp_line": sharp_line,
            "sharp_odds": sharp_odds,
            "pp_dk_edge": None,
            "pp_sharp_edge": None,
            "market_signal": "NEUTRAL",
            "pass": True
        }
        
        # Calculate edges
        if pp_line and dk_line:
            result["pp_dk_edge"] = round((dk_line - pp_line) / pp_line * 100, 2) if pp_line else None
            
        if pp_line and sharp_line:
            result["pp_sharp_edge"] = round((sharp_line - pp_line) / pp_line * 100, 2) if pp_line else None
        
        # Determine market signal
        # If sharp line > PP line, sharps think OVER is good
        if sharp_line and pp_line:
            if sharp_line > pp_line * 1.05:  # Sharp 5%+ higher = BULLISH
                result["market_signal"] = "BULLISH"
            elif sharp_line < pp_line * 0.95:  # Sharp 5%+ lower = BEARISH
                result["market_signal"] = "BEARISH"
                result["pass"] = False
                result["reason"] = "Sharp money disagrees (line 5%+ lower)"
        
        # Check implied probability from sharp odds
        if sharp_odds:
            if sharp_odds < -200:  # Heavy favorite
                result["market_signal"] = "STRONG_BULLISH"
            elif sharp_odds > 150:  # Underdog
                result["market_signal"] = "BEARISH"
                result["pass"] = False
                result["reason"] = f"Sharp odds unfavorable ({sharp_odds})"
        
        return result
    
    # =========================================================================
    # GATE 3: THE SCOUT (Weather, Park, Statcast, BvP)
    # =========================================================================
    
    async def get_weather(self, team_abbr: str) -> Optional[Dict[str, Any]]:
        """
        Fetch current weather for a venue.
        
        Uses Open-Meteo API (free, no key required).
        """
        coords = self.venue_coords.get(team_abbr)
        if not coords:
            return None
        
        lat, lon = coords
        
        try:
            async with httpx.AsyncClient() as client:
                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                    "hourly": "temperature_2m,precipitation_probability,windspeed_10m,winddirection_10m",
                    "temperature_unit": "fahrenheit",
                    "windspeed_unit": "mph",
                    "timezone": "America/New_York"
                }
                
                resp = await client.get(self.weather_api, params=params, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    current = data.get("current_weather", {})
                    
                    return {
                        "temperature": current.get("temperature"),
                        "windspeed": current.get("windspeed"),
                        "winddirection": current.get("winddirection"),
                        "is_day": current.get("is_day"),
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            logger.warning(f"[SCOUT] Weather fetch failed for {team_abbr}: {e}")
        
        return None
    
    def get_park_factor(self, team_abbr: str) -> Dict[str, Any]:
        """Get park factor data for a venue."""
        # Normalize team abbreviation
        team_upper = team_abbr.upper() if team_abbr else ""
        
        # Common team name mappings
        team_mappings = {
            "SAN DIEGO PADRES": "SD",
            "LOS ANGELES DODGERS": "LAD",
            "LOS ANGELES ANGELS": "LAA",
            "SAN FRANCISCO GIANTS": "SF",
            "NEW YORK YANKEES": "NYY",
            "NEW YORK METS": "NYM",
            "CHICAGO CUBS": "CHC",
            "CHICAGO WHITE SOX": "CHW",
            "BOSTON RED SOX": "BOS",
            "TAMPA BAY RAYS": "TB",
            "KANSAS CITY ROYALS": "KC",
            "ARIZONA DIAMONDBACKS": "ARI",
            "COLORADO ROCKIES": "COL",
            "ATLANTA BRAVES": "ATL",
            "MIAMI MARLINS": "MIA",
            "PHILADELPHIA PHILLIES": "PHI",
            "WASHINGTON NATIONALS": "WSH",
            "PITTSBURGH PIRATES": "PIT",
            "CINCINNATI REDS": "CIN",
            "MILWAUKEE BREWERS": "MIL",
            "ST. LOUIS CARDINALS": "STL",
            "HOUSTON ASTROS": "HOU",
            "TEXAS RANGERS": "TEX",
            "SEATTLE MARINERS": "SEA",
            "OAKLAND ATHLETICS": "OAK",
            "MINNESOTA TWINS": "MIN",
            "CLEVELAND GUARDIANS": "CLE",
            "DETROIT TIGERS": "DET",
            "TORONTO BLUE JAYS": "TOR",
            "BALTIMORE ORIOLES": "BAL",
        }
        
        # Check if it's a full team name
        if team_upper in team_mappings:
            team_upper = team_mappings[team_upper]
        
        park = PARK_FACTORS.get(team_upper, DEFAULT_PARK)
        return {
            "team": team_upper,
            "name": park["name"],
            "factor": park["factor"],
            "hr_factor": park["hr_factor"],
            "type": park["type"],
            "altitude": park["altitude"],
            "is_hitter_friendly": park["factor"] >= 1.05,
            "is_pitcher_friendly": park["factor"] <= 0.95
        }
    
    async def get_statcast_data(self, player_name: str, is_pitcher: bool = False) -> Dict[str, Any]:
        """
        Get Statcast-style data from master hub.
        
        For batters: Barrel %, Hard Hit Rate (L5)
        For pitchers: Velocity, Spin Rate trends
        """
        player = await self.master_hub.find_one(
            {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
            {"_id": 0}
        )
        
        if not player:
            return {"available": False, "reason": "Player not found in master hub"}
        
        game_logs = player.get("bdl_game_logs", [])[:5]  # Last 5 games
        
        if is_pitcher:
            # Pitcher analysis
            return {
                "available": True,
                "player_type": "pitcher",
                "games_analyzed": len(game_logs),
                "k_per_9": player.get("k_per_9"),
                "whip": player.get("whip"),
                "era": player.get("era"),
                # Note: Actual velocity/spin would come from Statcast API
                "velocity_trend": "stable",  # Placeholder
                "spin_trend": "stable",      # Placeholder
            }
        else:
            # Batter analysis - calculate from game logs
            if not game_logs:
                return {"available": False, "reason": "No recent game logs"}
            
            # Calculate L5 stats
            total_abs = sum(g.get("at_bats", 0) or 0 for g in game_logs)
            total_hits = sum(g.get("hits", 0) or 0 for g in game_logs)
            total_hrs = sum(g.get("home_runs", 0) or 0 for g in game_logs)
            total_tbs = sum(g.get("total_bases", 0) or 0 for g in game_logs)
            
            avg_l5 = round(total_hits / total_abs, 3) if total_abs > 0 else 0
            iso_l5 = round((total_tbs - total_hits) / total_abs, 3) if total_abs > 0 else 0
            
            # Estimate barrel % from ISO (rough proxy)
            # Elite ISO > .200, Good ISO > .150
            estimated_barrel = min(15.0, iso_l5 * 50) if iso_l5 else 0
            
            # Hard hit rate estimate (based on extra base hits)
            xbh = total_tbs - total_hits
            hard_hit_est = min(50.0, (xbh / max(total_abs, 1)) * 200)
            
            return {
                "available": True,
                "player_type": "batter",
                "games_analyzed": len(game_logs),
                "l5_avg": avg_l5,
                "l5_iso": iso_l5,
                "l5_hrs": total_hrs,
                "estimated_barrel_pct": round(estimated_barrel, 1),
                "estimated_hard_hit_pct": round(hard_hit_est, 1),
                "is_hot": avg_l5 >= 0.300 and iso_l5 >= 0.200,
                "is_cold": avg_l5 <= 0.150
            }
    
    async def run_scout_gate(
        self, 
        prop: Dict,
        home_team: str,
        player_name: str,
        stat_type: str
    ) -> Dict[str, Any]:
        """
        Run the complete Scout gate analysis.
        
        Combines weather, park factor, and Statcast data.
        Returns trap flags if conditions invalidate the prop.
        """
        result = {
            "gate": "SCOUT",
            "weather": None,
            "park": None,
            "statcast": None,
            "traps": [],
            "pass": True
        }
        
        # 1. Get park factor
        result["park"] = self.get_park_factor(home_team)
        
        # 2. Get weather (only for outdoor venues)
        if result["park"]["type"] == "outdoor":
            result["weather"] = await self.get_weather(home_team)
            
            if result["weather"]:
                wind = result["weather"].get("windspeed", 0)
                temp = result["weather"].get("temperature", 70)
                wind_dir = result["weather"].get("winddirection", 0)
                
                # Check weather traps
                stat_lower = stat_type.lower()
                
                # HR trap: High wind blowing in
                if "home run" in stat_lower and wind > WEATHER_TRAPS["wind_hr_trap"]:
                    # Wind direction 180-360 = blowing in (roughly)
                    if 135 < wind_dir < 315:
                        result["traps"].append({
                            "type": "WEATHER_HR_TRAP",
                            "reason": f"Wind {wind}mph blowing IN - HR suppressed",
                            "severity": "HIGH"
                        })
                        result["pass"] = False
                
                # Cold weather trap
                if temp < WEATHER_TRAPS["cold_temp_trap"]:
                    result["traps"].append({
                        "type": "COLD_WEATHER",
                        "reason": f"Temperature {temp}°F - offense typically suppressed",
                        "severity": "MEDIUM"
                    })
        
        # 3. Get Statcast data
        is_pitcher = "pitcher" in stat_type.lower() or "strikeout" in stat_type.lower()
        result["statcast"] = await self.get_statcast_data(player_name, is_pitcher)
        
        if result["statcast"].get("available"):
            if result["statcast"].get("player_type") == "batter":
                # Check if batter is cold
                if result["statcast"].get("is_cold"):
                    result["traps"].append({
                        "type": "COLD_BATTER",
                        "reason": f"L5 AVG {result['statcast'].get('l5_avg')} - batter in slump",
                        "severity": "HIGH"
                    })
                    result["pass"] = False
                
                # Check barrel rate for HR/TB props
                stat_lower = stat_type.lower()
                if "home run" in stat_lower or "total bases" in stat_lower:
                    barrel = result["statcast"].get("estimated_barrel_pct", 0)
                    if barrel < STATCAST_THRESHOLDS["good_barrel_pct"]:
                        result["traps"].append({
                            "type": "LOW_BARREL_RATE",
                            "reason": f"Estimated barrel {barrel}% below threshold",
                            "severity": "MEDIUM"
                        })
        
        # 4. Park factor adjustment for HR props
        stat_lower = stat_type.lower()
        if "home run" in stat_lower:
            hr_factor = result["park"].get("hr_factor", 1.0)
            if hr_factor < 0.85:
                result["traps"].append({
                    "type": "PITCHER_PARK_HR",
                    "reason": f"Park HR factor {hr_factor} - home runs suppressed",
                    "severity": "HIGH"
                })
                result["pass"] = False
        
        return result
    
    # =========================================================================
    # GATE 4: THE BRAIN (Oracle Verdict)
    # =========================================================================
    
    async def get_oracle_verdict(self, prop: Dict) -> Dict[str, Any]:
        """
        Get Oracle verdict from propvision_oracle_service.
        
        Returns the Bull vs Bear analysis result.
        """
        from services.propvision_oracle_service import get_oracle_service
        
        oracle = get_oracle_service(self.db)
        oracle.sport = "mlb"
        
        # Synthesize data
        synth = await oracle.oracle_data_synthesis(prop)
        
        # Get verdict
        verdict = await oracle.oracle_final_verdict(
            vk_projection=synth.get("vk_projection"),
            pinnacle_devig_prob=synth.get("pinnacle_devig_prob"),
            dk_ladder=synth.get("dk_ladder"),
            prop=prop
        )
        
        verdict["gate"] = "BRAIN"
        verdict["pass"] = verdict.get("oracle_score", 0) >= 7
        
        return verdict
    
    # =========================================================================
    # MAIN PIPELINE: RUN ALL 4 GATES + BADGES
    # =========================================================================
    
    async def analyze_prop(self, prop: Dict) -> Dict[str, Any]:
        """
        Run a prop through all 4 gates with MLB badge evaluation.
        
        Returns:
            Complete analysis with gate results, badges, and final verdict
        """
        from services.mlb_badge_system import get_mlb_badge_service, MLBOracleWeighting
        
        player_name = prop.get("player_name")
        stat_type = prop.get("stat_type")
        home_team = prop.get("home_team") or prop.get("team")
        opponent_pitcher = prop.get("opponent_pitcher")
        
        result = {
            "player_name": player_name,
            "stat_type": stat_type,
            "line": prop.get("line"),
            "recommendation": prop.get("recommendation"),
            "gates": {},
            "gates_passed": 0,
            "total_gates": 4,
            "badges": [],
            "badge_boost": 1.0,
            "final_verdict": "PENDING",
            "traps_detected": [],
            "oracle_priority": "Standard",
            "analyzed_at": datetime.now(timezone.utc).isoformat()
        }
        
        # GATE 1: THE MATH
        result["gates"]["math"] = await self.get_vk_data(player_name, stat_type)
        if result["gates"]["math"].get("pass"):
            result["gates_passed"] += 1
        
        # GATE 2: THE MARKET
        result["gates"]["market"] = self.get_market_data(prop)
        if result["gates"]["market"].get("pass"):
            result["gates_passed"] += 1
        
        # GATE 3: THE SCOUT (with weather)
        park = self.get_park_factor(home_team or "NYY")
        weather = None
        if park.get("type") == "outdoor":
            weather = await self.get_weather(home_team or "NYY")
        
        result["gates"]["scout"] = await self.run_scout_gate(
            prop, home_team or "NYY", player_name, stat_type
        )
        if result["gates"]["scout"].get("pass"):
            result["gates_passed"] += 1
        
        # Collect traps from scout gate
        result["traps_detected"] = result["gates"]["scout"].get("traps", [])
        
        # =====================================================================
        # MLB BADGE EVALUATION
        # =====================================================================
        badge_service = get_mlb_badge_service(self.db)
        
        badges = await badge_service.evaluate_all_badges(
            player_name=player_name,
            stat_type=stat_type,
            prop=prop,
            weather=weather,
            park=park,
            opponent_pitcher=opponent_pitcher,
            umpire_data=None  # Would need umpire API integration
        )
        
        result["badges"] = badges
        
        # Calculate badge boost (multiply all badge boosts)
        badge_boost = 1.0
        for badge in badges:
            badge_boost *= badge.get("boost", 1.0)
        result["badge_boost"] = round(badge_boost, 3)
        
        # Check for trap badges (High-Heat, Cold Zone)
        for badge in badges:
            if badge.get("boost", 1.0) < 1.0:
                result["traps_detected"].append({
                    "type": f"BADGE_{badge.get('id', 'unknown').upper()}",
                    "reason": badge.get("description"),
                    "severity": "HIGH" if badge.get("boost", 1.0) < 0.90 else "MEDIUM"
                })
        
        # =====================================================================
        # GATE 4: THE BRAIN (with Oracle Weighting)
        # =====================================================================
        # Check for BvP dominance
        bvp_badge = next((b for b in badges if b.get("id") == "bvp_dominator"), None)
        split_badge = next((b for b in badges if b.get("id") == "split_advantage"), None)
        
        has_bvp = bvp_badge is not None
        result["oracle_priority"] = "BvP" if has_bvp else ("Split Dominance" if split_badge else "Standard")
        
        result["gates"]["brain"] = await self.get_oracle_verdict(prop)
        
        # Apply weighted scoring if badges present
        if badges:
            # base_score used in weighted calculation
            bvp_score = 8 if bvp_badge else None
            split_score = 7 if split_badge else 5
            vk_score = 7 if result["gates"]["math"].get("pass") else 4
            market_score = 7 if result["gates"]["market"].get("pass") else 4
            
            weighted = MLBOracleWeighting.calculate_weighted_score(
                bvp_score=bvp_score,
                split_score=split_score,
                vk_score=vk_score,
                market_score=market_score,
                badge_boost=badge_boost,
                has_bvp=has_bvp
            )
            
            result["gates"]["brain"]["weighted_score"] = weighted
            result["gates"]["brain"]["oracle_score"] = weighted["final_score"]
        
        if result["gates"]["brain"].get("oracle_score", 0) >= 7:
            result["gates_passed"] += 1
        
        # =====================================================================
        # FINAL VERDICT
        # =====================================================================
        high_severity_traps = [t for t in result["traps_detected"] if t.get("severity") == "HIGH"]
        
        if result["gates_passed"] == 4 and len(high_severity_traps) == 0:
            result["final_verdict"] = "ELITE_PLAY"
        elif result["gates_passed"] >= 3 and len(high_severity_traps) == 0:
            result["final_verdict"] = "SOLID_PLAY"
        elif len(high_severity_traps) > 0:
            result["final_verdict"] = "TRAP"
        elif result["gates_passed"] >= 2:
            result["final_verdict"] = "LEAN"
        else:
            result["final_verdict"] = "AVOID"
        
        return result
    
    async def analyze_tier_props(self, tier: str = "safe_haven", limit: int = 10) -> Dict[str, Any]:
        """
        Analyze all props in a tier through the 4-gate system.
        
        Args:
            tier: Which tier to analyze (safe_haven, front_lines, war_zone)
            limit: Max props to analyze
            
        Returns:
            Dict with analyzed props and summary
        """
        # Map tier to collection
        tier_collections = {
            "safe_haven": "mlb_goblins",
            "front_lines": "mlb_hrr_picks",
            "war_zone": "mlb_demons"
        }
        
        collection_name = tier_collections.get(tier)
        if not collection_name:
            return {"error": f"Unknown tier: {tier}"}
        
        collection = self.db[collection_name]
        props = await collection.find({}, {"_id": 0}).limit(limit).to_list(length=limit)
        
        results = {
            "tier": tier,
            "total_props": len(props),
            "analyzed": [],
            "summary": {
                "elite_plays": 0,
                "solid_plays": 0,
                "leans": 0,
                "traps": 0,
                "avoids": 0
            }
        }
        
        for prop in props:
            analysis = await self.analyze_prop(prop)
            results["analyzed"].append(analysis)
            
            # Update summary
            verdict = analysis["final_verdict"]
            if verdict == "ELITE_PLAY":
                results["summary"]["elite_plays"] += 1
            elif verdict == "SOLID_PLAY":
                results["summary"]["solid_plays"] += 1
            elif verdict == "LEAN":
                results["summary"]["leans"] += 1
            elif verdict == "TRAP":
                results["summary"]["traps"] += 1
            else:
                results["summary"]["avoids"] += 1
        
        return results


# Singleton instance
_four_gate_service: Optional[MLBFourGateSystem] = None


def get_four_gate_service(db: AsyncIOMotorDatabase) -> MLBFourGateSystem:
    """Get or create Four Gate service instance."""
    global _four_gate_service
    if _four_gate_service is None:
        _four_gate_service = MLBFourGateSystem(db)
    return _four_gate_service
