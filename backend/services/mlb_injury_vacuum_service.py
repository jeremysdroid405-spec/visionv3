"""
MLB InjuryVacuumService - Usage Vacuum Microservice for MLB
============================================================
Event-driven service that monitors MLB injury reports and calculates
"Usage Vacuum" beneficiaries when star players are on IL or DTD.

Architecture:
- Monitors MLB injury reports from ESPN and other sources
- Calculates lineup impact when key players are OUT
- Identifies beneficiaries (replacement starters, lineup movers)
- Applies modifiers for batting order changes

MLB-Specific Considerations:
- IL (Injured List) vs DTD (Day-to-Day) status
- Lineup order impacts (moving up in order = more ABs)
- Platoon advantages when regulars are out
- Pitcher impact on batter prop values

Author: PropVision AI
Version: 1.0.0
"""
import asyncio
import aiohttp
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import logging
import json
import re
import hashlib

logger = logging.getLogger(__name__)

# =============================================================================
# MLB USAGE VACUUM CONSTANTS
# =============================================================================

# Star player threshold for late scratch detection
STAR_THRESHOLD_OPS = 0.800  # OPS threshold for "star" hitters
STAR_THRESHOLD_WAR = 2.0    # WAR threshold

# Score modifiers
PRIMARY_BENEFICIARY_MODIFIER = 12.0
SECONDARY_BENEFICIARY_MODIFIER = 8.0

# Late scratch window (90 minutes for MLB)
LATE_SCRATCH_WINDOW_MINUTES = 90

# Injury status triggers
TRIGGER_STATUSES = ["OUT", "IL", "INJURED", "DTD"]

# ESPN MLB Injury URL
ESPN_MLB_INJURY_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"

# MLB Team mappings
MLB_TEAM_NAME_TO_ABBREV = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH"
}

# =============================================================================
# MLB STAR PROFILES (High-Impact Players)
# =============================================================================

MLB_STAR_PROFILES = {
    # Format: "player_name": {"team": "ABBR", "ops": X.XXX, "position": "POS", "bat_order": X}
    "Shohei Ohtani": {"team": "LAD", "ops": 1.050, "position": "DH", "bat_order": 2},
    "Mookie Betts": {"team": "LAD", "ops": 0.920, "position": "SS", "bat_order": 1},
    "Aaron Judge": {"team": "NYY", "ops": 1.020, "position": "RF", "bat_order": 2},
    "Juan Soto": {"team": "NYY", "ops": 0.950, "position": "LF", "bat_order": 3},
    "Ronald Acuna Jr.": {"team": "ATL", "ops": 0.980, "position": "RF", "bat_order": 1},
    "Corey Seager": {"team": "TEX", "ops": 0.900, "position": "SS", "bat_order": 3},
    "Freddie Freeman": {"team": "LAD", "ops": 0.910, "position": "1B", "bat_order": 3},
    "Trea Turner": {"team": "PHI", "ops": 0.850, "position": "SS", "bat_order": 1},
    "Francisco Lindor": {"team": "NYM", "ops": 0.870, "position": "SS", "bat_order": 1},
    "Marcus Semien": {"team": "TEX", "ops": 0.820, "position": "2B", "bat_order": 1},
    "Julio Rodriguez": {"team": "SEA", "ops": 0.840, "position": "CF", "bat_order": 2},
    "Bobby Witt Jr.": {"team": "KC", "ops": 0.880, "position": "SS", "bat_order": 2},
    "Gunnar Henderson": {"team": "BAL", "ops": 0.890, "position": "SS", "bat_order": 2},
    "Elly De La Cruz": {"team": "CIN", "ops": 0.830, "position": "SS", "bat_order": 1},
    "Corbin Carroll": {"team": "ARI", "ops": 0.820, "position": "CF", "bat_order": 1},
    "Mike Trout": {"team": "LAA", "ops": 0.900, "position": "CF", "bat_order": 2},
    "Bryce Harper": {"team": "PHI", "ops": 0.920, "position": "1B", "bat_order": 3},
    "Vladimir Guerrero Jr.": {"team": "TOR", "ops": 0.870, "position": "1B", "bat_order": 3},
    "Rafael Devers": {"team": "BOS", "ops": 0.880, "position": "3B", "bat_order": 3},
    "Jose Ramirez": {"team": "CLE", "ops": 0.880, "position": "3B", "bat_order": 3},
    "Matt Olson": {"team": "ATL", "ops": 0.850, "position": "1B", "bat_order": 4},
    "Pete Alonso": {"team": "NYM", "ops": 0.840, "position": "1B", "bat_order": 4},
    "Kyle Tucker": {"team": "HOU", "ops": 0.890, "position": "RF", "bat_order": 3},
    "Yordan Alvarez": {"team": "HOU", "ops": 0.950, "position": "DH", "bat_order": 4},
    "Manny Machado": {"team": "SD", "ops": 0.850, "position": "3B", "bat_order": 3},
    "Fernando Tatis Jr.": {"team": "SD", "ops": 0.870, "position": "RF", "bat_order": 2},
}

# =============================================================================
# BENEFICIARY MAPPINGS (Who benefits when star is OUT)
# =============================================================================

MLB_BENEFICIARY_MAPPINGS = {
    # Format: "injured_star": [("beneficiary", ab_bump, lineup_bump), ...]
    "Shohei Ohtani": [
        ("Teoscar Hernandez", 0.5, 1),
        ("Max Muncy", 0.3, 1),
    ],
    "Mookie Betts": [
        ("Teoscar Hernandez", 0.5, 2),
        ("Gavin Lux", 0.4, 1),
    ],
    "Aaron Judge": [
        ("Giancarlo Stanton", 0.5, 1),
        ("Anthony Volpe", 0.3, 1),
    ],
    "Juan Soto": [
        ("Giancarlo Stanton", 0.4, 1),
        ("Austin Wells", 0.3, 1),
    ],
    "Ronald Acuna Jr.": [
        ("Ozzie Albies", 0.5, 2),
        ("Michael Harris II", 0.4, 1),
    ],
    "Corey Seager": [
        ("Marcus Semien", 0.4, 1),
        ("Wyatt Langford", 0.3, 1),
    ],
    "Freddie Freeman": [
        ("Teoscar Hernandez", 0.4, 1),
        ("Max Muncy", 0.3, 1),
    ],
    "Mike Trout": [
        ("Taylor Ward", 0.5, 2),
        ("Nolan Schanuel", 0.3, 1),
    ],
    "Bryce Harper": [
        ("Kyle Schwarber", 0.4, 1),
        ("Alec Bohm", 0.3, 1),
    ],
    "Yordan Alvarez": [
        ("Kyle Tucker", 0.4, 1),
        ("Jose Altuve", 0.3, 1),
    ],
    "Fernando Tatis Jr.": [
        ("Manny Machado", 0.4, 1),
        ("Jake Cronenworth", 0.3, 1),
    ],
}


class MLBInjuryVacuumService:
    """MLB-specific Injury Vacuum Service."""
    
    def __init__(self, db=None, redis_client=None):
        """Initialize MLB Injury Vacuum Service."""
        self.db = db
        self.redis = redis_client
        
        # In-memory caches
        self.star_profiles_cache: Dict[str, Dict] = {}
        self.injury_status_cache: Dict[str, Dict] = {}
        self.beneficiary_cache: Dict[str, List[Dict]] = {}
        self.active_vacuums: Dict[str, Dict] = {}
        
        # Timestamps
        self.last_injury_check: Optional[datetime] = None
        self.last_vacuum_update: Optional[datetime] = None
        
        # MongoDB collections
        self.injury_log = None
        self.vacuum_alerts = None
        if db is not None:
            try:
                self.injury_log = db.mlb_injury_log
                self.vacuum_alerts = db.mlb_vacuum_alerts
            except Exception:
                pass
        
        # Initialize with star profiles
        for name, profile in MLB_STAR_PROFILES.items():
            self.star_profiles_cache[self._normalize_player_name(name)] = {
                **profile,
                "name": name
            }
    
    async def _fetch_json(self, url: str, timeout: int = 15) -> Optional[Dict]:
        """Fetch JSON from URL with error handling."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"[MLBVacuum] HTTP {response.status} for {url}")
                        return None
        except asyncio.TimeoutError:
            logger.error(f"[MLBVacuum] Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"[MLBVacuum] Error fetching {url}: {e}")
            return None
    
    def _normalize_player_name(self, name: str) -> str:
        """Normalize player name for matching."""
        if not name:
            return ""
        name = re.sub(r'\s+(Jr\.?|Sr\.?|III|II|IV)$', '', name, flags=re.IGNORECASE)
        return " ".join(name.strip().split()).lower()
    
    def _is_star_player(self, player_name: str) -> Tuple[bool, Optional[Dict]]:
        """Check if player is a star (high OPS or WAR)."""
        normalized = self._normalize_player_name(player_name)
        
        if normalized in self.star_profiles_cache:
            profile = self.star_profiles_cache[normalized]
            return profile.get("ops", 0) >= STAR_THRESHOLD_OPS, profile
        
        for star_name, profile in MLB_STAR_PROFILES.items():
            if self._normalize_player_name(star_name) == normalized:
                return True, {**profile, "name": star_name}
        
        return False, None
    
    def _get_beneficiaries(self, injured_player: str) -> List[Dict]:
        """Get beneficiaries for an injured star player."""
        normalized = self._normalize_player_name(injured_player)
        
        for star_name, beneficiaries in MLB_BENEFICIARY_MAPPINGS.items():
            if self._normalize_player_name(star_name) == normalized:
                result = []
                for i, beneficiary_data in enumerate(beneficiaries[:2]):
                    beneficiary_name = beneficiary_data[0]
                    ab_bump = beneficiary_data[1]
                    lineup_bump = beneficiary_data[2] if len(beneficiary_data) > 2 else 1
                    
                    modifier = PRIMARY_BENEFICIARY_MODIFIER if i == 0 else SECONDARY_BENEFICIARY_MODIFIER
                    
                    result.append({
                        "name": beneficiary_name,
                        "ab_bump": ab_bump,
                        "lineup_bump": lineup_bump,
                        "modifier": modifier,
                        "rank": "primary" if i == 0 else "secondary",
                        "high_usage_advantage": True,
                        "late_injury_boost": True
                    })
                return result
        
        return []
    
    async def fetch_espn_injuries(self) -> List[Dict]:
        """Fetch current MLB injuries from ESPN."""
        logger.info("[MLBVacuum] Fetching ESPN MLB injuries...")
        
        data = await self._fetch_json(ESPN_MLB_INJURY_URL)
        if not data:
            return []
        
        injuries = []
        
        try:
            for team_data in data.get("items", []):
                team_name = team_data.get("team", {}).get("displayName", "Unknown")
                team_abbrev = MLB_TEAM_NAME_TO_ABBREV.get(team_name, team_name[:3].upper())
                
                for injury in team_data.get("injuries", []):
                    athlete = injury.get("athlete", {})
                    player_name = athlete.get("displayName", "Unknown")
                    status = injury.get("status", "Unknown")
                    injury_type = injury.get("type", {}).get("description", "Unknown")
                    details = injury.get("details", {}).get("detail", "")
                    
                    injuries.append({
                        "player_name": player_name,
                        "team": team_abbrev,
                        "team_full": team_name,
                        "status": status.upper(),
                        "injury_type": injury_type,
                        "details": details,
                        "source": "ESPN",
                        "fetched_at": datetime.now(timezone.utc).isoformat()
                    })
            
            logger.info(f"[MLBVacuum] Fetched {len(injuries)} MLB injuries from ESPN")
            
        except Exception as e:
            logger.error(f"[MLBVacuum] Error parsing ESPN injuries: {e}")
        
        return injuries
    
    async def fetch_bdl_injuries(self) -> List[Dict]:
        """Fetch current MLB injuries from BallDontLie API (primary source)."""
        import os
        logger.info("[MLBVacuum] Fetching BDL MLB injuries...")
        
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        if not api_key:
            logger.warning("[MLBVacuum] No BDL API key for injuries")
            return []
        
        injuries = []
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.balldontlie.io/mlb/v1/player_injuries",
                    params={"per_page": 100},
                    headers={"Authorization": api_key},
                    timeout=15
                ) as response:
                    if response.status != 200:
                        logger.warning(f"[MLBVacuum] BDL injuries HTTP {response.status}")
                        return []
                    
                    data = await response.json()
                    
                    for inj in data.get("data", []):
                        player = inj.get("player", {})
                        team = player.get("team", {})
                        player_name = player.get("full_name", "Unknown")
                        
                        # Map BDL status to our trigger statuses
                        bdl_status = inj.get("status", "").upper()
                        # BDL uses: "10-Day-IL", "60-Day-IL", "15-Day-IL", "Day-To-Day", "Out"
                        if "IL" in bdl_status or bdl_status == "OUT":
                            status = "OUT"
                        elif "DAY-TO-DAY" in bdl_status or bdl_status == "DTD":
                            status = "DTD"
                        else:
                            status = bdl_status
                        
                        injuries.append({
                            "player_name": player_name,
                            "team": team.get("abbreviation", "???"),
                            "team_full": team.get("display_name", "Unknown"),
                            "status": status,
                            "injury_type": inj.get("type", "Unknown"),
                            "details": inj.get("short_comment", ""),
                            "long_details": inj.get("long_comment", ""),
                            "source": "BDL",
                            "return_date": inj.get("return_date"),
                            "fetched_at": datetime.now(timezone.utc).isoformat()
                        })
            
            logger.info(f"[MLBVacuum] Fetched {len(injuries)} MLB injuries from BDL")
            
        except Exception as e:
            logger.error(f"[MLBVacuum] Error fetching BDL injuries: {e}")
        
        return injuries
    
    async def check_injuries(self) -> Dict[str, Any]:
        """Check for MLB injuries and trigger vacuums for stars.
        
        Uses BDL as primary source, falls back to ESPN if needed.
        """
        logger.info("[MLBVacuum] Checking MLB injuries...")
        
        # Try BDL first (more reliable), fallback to ESPN
        injuries = await self.fetch_bdl_injuries()
        if not injuries:
            logger.info("[MLBVacuum] No BDL injuries, trying ESPN...")
            injuries = await self.fetch_espn_injuries()
        
        self.last_injury_check = datetime.now(timezone.utc)
        
        triggered_vacuums = []
        status_changes = []
        all_star_injuries = []  # Track all star injuries for display
        
        for injury in injuries:
            player_name = injury.get("player_name", "")
            current_status = injury.get("status", "")
            
            # Check if this is a star player
            is_star, profile = self._is_star_player(player_name)
            
            if not is_star:
                continue
            
            # Track all star injuries (even if no beneficiaries)
            all_star_injuries.append({
                "player": player_name,
                "team": injury.get("team"),
                "status": current_status,
                "injury_type": injury.get("injury_type"),
                "details": injury.get("details"),
                "source": injury.get("source")
            })
            
            # Check if status triggers a vacuum
            if current_status in TRIGGER_STATUSES:
                normalized = self._normalize_player_name(player_name)
                
                # Check if this is a new vacuum or status change
                cached_status = self.injury_status_cache.get(normalized, {}).get("status")
                
                if cached_status != current_status:
                    status_changes.append({
                        "player": player_name,
                        "old_status": cached_status,
                        "new_status": current_status
                    })
                
                # Update cache
                self.injury_status_cache[normalized] = {
                    "status": current_status,
                    "injury": injury,
                    "updated_at": datetime.now(timezone.utc)
                }
                
                # Get beneficiaries
                beneficiaries = self._get_beneficiaries(player_name)
                
                if beneficiaries:
                    vacuum = {
                        "injured_player": player_name,
                        "injured_team": injury.get("team"),
                        "injury_type": injury.get("injury_type"),
                        "injury_details": injury.get("details"),
                        "status": current_status,
                        "profile": profile,
                        "beneficiaries": beneficiaries,
                        "triggered_at": datetime.now(timezone.utc).isoformat(),
                        "is_late_scratch": True  # Consider all current-day injuries as late
                    }
                    
                    self.active_vacuums[normalized] = vacuum
                    triggered_vacuums.append(vacuum)
                    
                    logger.info(f"[MLBVacuum] Triggered vacuum for {player_name} ({current_status})")
        
        # Save to database
        if self.vacuum_alerts is not None and triggered_vacuums:
            try:
                for vacuum in triggered_vacuums:
                    await self.vacuum_alerts.update_one(
                        {"injured_player": vacuum["injured_player"]},
                        {"$set": vacuum},
                        upsert=True
                    )
            except Exception as e:
                logger.error(f"[MLBVacuum] Error saving to DB: {e}")
        
        return {
            "success": True,
            "injuries_checked": len(injuries),
            "stars_out": len(triggered_vacuums),
            "star_injuries": all_star_injuries,  # All star injuries for display
            "triggered_vacuums": triggered_vacuums,
            "status_changes": status_changes,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_active_vacuums_for_today(self) -> List[Dict]:
        """Get all active vacuums for today's games."""
        return list(self.active_vacuums.values())
    
    async def get_vacuum_updates(self) -> Dict[str, Any]:
        """Get current vacuum state for the UI."""
        return {
            "active_count": len(self.active_vacuums),
            "vacuums": list(self.active_vacuums.values()),
            "last_check": self.last_injury_check.isoformat() if self.last_injury_check else None,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def get_live_alerts(self, refresh: bool = False) -> List[Dict]:
        """
        Get formatted alerts for the "Live Injury Advantage" section.
        """
        if refresh or not self.last_injury_check:
            await self.check_injuries()
        
        alerts = []
        
        for vacuum in self.active_vacuums.values():
            injured_player = vacuum.get("injured_player")
            profile = vacuum.get("profile", {})
            
            # Calculate time ago
            triggered_at = vacuum.get("triggered_at")
            if triggered_at:
                try:
                    triggered_dt = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
                    mins_ago = int((datetime.now(timezone.utc) - triggered_dt).total_seconds() / 60)
                    if mins_ago < 60:
                        time_ago = f"{mins_ago}m ago"
                    else:
                        time_ago = f"{mins_ago // 60}h {mins_ago % 60}m ago"
                except:
                    time_ago = "Recently"
            else:
                time_ago = "Recently"
            
            # Create injury reason headline
            injury_type = vacuum.get("injury_type", "")
            injury_details = vacuum.get("injury_details", "")
            injury_reason = f"{injury_type}: {injury_details}" if injury_details else injury_type
            
            for beneficiary in vacuum.get("beneficiaries", []):
                alerts.append({
                    "id": f"{injured_player}-{beneficiary['name']}",
                    "injured_player": injured_player,
                    "injured_team": vacuum.get("injured_team"),
                    "injured_ops": profile.get("ops", 0),
                    "injury_reason": injury_reason,
                    "time_ago": time_ago,
                    "is_late_scratch": vacuum.get("is_late_scratch", True),
                    "beneficiary_name": beneficiary["name"],
                    "ab_bump": beneficiary.get("ab_bump", 0),
                    "lineup_bump": beneficiary.get("lineup_bump", 0),
                    "modifier": beneficiary.get("modifier", 0),
                    "rank": beneficiary.get("rank", "secondary"),
                    "late_injury_boost": beneficiary.get("late_injury_boost", True)
                })
        
        return alerts
    
    async def clear_vacuum(self, injured_player: str) -> Dict[str, Any]:
        """Clear a vacuum when player returns."""
        normalized = self._normalize_player_name(injured_player)
        
        if normalized in self.active_vacuums:
            del self.active_vacuums[normalized]
            
            if normalized in self.injury_status_cache:
                del self.injury_status_cache[normalized]
            
            return {
                "success": True,
                "message": f"Vacuum cleared for {injured_player}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        return {
            "success": False,
            "message": f"No active vacuum found for {injured_player}"
        }


# =============================================================================
# SINGLETON PATTERN
# =============================================================================

_mlb_vacuum_service: Optional[MLBInjuryVacuumService] = None


def get_mlb_vacuum_service(db=None, redis=None) -> MLBInjuryVacuumService:
    """Get or create MLB Vacuum Service instance."""
    global _mlb_vacuum_service
    if _mlb_vacuum_service is None:
        _mlb_vacuum_service = MLBInjuryVacuumService(db, redis)
    return _mlb_vacuum_service
