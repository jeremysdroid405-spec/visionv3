"""
BDL Player Badge Service
========================
Generates player performance badges using BallDontLie Advanced Stats API.

REPLACES: nba_career_service.py (deprecated due to stats.nba.com bot protection)

BDL Endpoints Used:
- /season_averages/general?type=advanced  -> Usage%, PIE, True Shooting%
- /season_averages/tracking?type=drives   -> Drive stats
- /season_averages/tracking?type=passing  -> Passing stats  
- /season_averages/tracking?type=catchshoot -> Catch & Shoot stats
- /season_averages/hustle                  -> Deflections, Contested Shots
- /season_averages/playtype?type=prballhandler -> P&R efficiency
- /season_averages/playtype?type=isolation -> Isolation efficiency
- /season_averages/playtype?type=postup    -> Post-Up efficiency

Badge Types Generated:
1. [Volume Scorer]    - High usage% (>25%)
2. [Efficient]        - High TS% (>60%) or PIE (>15%)
3. [Playmaker]        - High AST% or passing stats
4. [Floor General]    - Elite AST/TO ratio in playtype
5. [3PT Assassin]     - High catch & shoot efficiency
6. [Motor]            - Top hustle stats (deflections, contested shots)
7. [Paint Beast]      - High post-up or P&R efficiency
8. [Shot Creator]     - High isolation efficiency
9. [Two-Way]          - Good defensive + offensive ratings
10. [Closer]          - High clutch efficiency (4th quarter)
"""

import logging
import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from motor.motor_asyncio import AsyncIOMotorDatabase
import httpx

from services.config.collection_names import COLL

logger = logging.getLogger(__name__)

# BDL Configuration
BDL_API_KEY = os.environ.get("BDL_API_KEY")
BDL_BASE_URL = "https://api.balldontlie.io/nba/v1"
CURRENT_SEASON = 2025

# Cache duration for badge data
BADGE_CACHE_HOURS = 12

# Badge Definitions with thresholds
BADGE_DEFINITIONS = {
    "volume_scorer": {
        "display": "Volume Scorer",
        "icon": "Flame",
        "color": "#ef4444",  # Red
        "description": "High usage rate player - touches the ball a lot",
        "threshold": {"usage_pct": 25.0}  # Top usage players
    },
    "efficient": {
        "display": "Efficient",
        "icon": "Target",
        "color": "#22c55e",  # Green
        "description": "Elite true shooting or high impact",
        "threshold": {"ts_pct": 60.0, "pie": 15.0}
    },
    "playmaker": {
        "display": "Playmaker",
        "icon": "Share2",
        "color": "#3b82f6",  # Blue
        "description": "Creates opportunities for teammates",
        "threshold": {"ast_pct": 25.0, "potential_ast": 8.0}
    },
    "floor_general": {
        "display": "Floor General",
        "icon": "Crown",
        "color": "#a855f7",  # Purple
        "description": "Elite ball handler with vision",
        "threshold": {"ast_to_ratio": 2.5}
    },
    "3pt_assassin": {
        "display": "3PT Assassin",
        "icon": "Crosshair",
        "color": "#06b6d4",  # Cyan
        "description": "Deadly from three-point range",
        "threshold": {"catch_shoot_fg3_pct": 40.0, "catch_shoot_fg3a": 3.0}
    },
    "motor": {
        "display": "Motor",
        "icon": "Zap",
        "color": "#f59e0b",  # Amber
        "description": "High effort player - deflections, contests",
        "threshold": {"deflections": 2.0, "contested_shots": 8.0}
    },
    "paint_beast": {
        "display": "Paint Beast",
        "icon": "Shield",
        "color": "#dc2626",  # Red-600
        "description": "Dominates in the paint",
        "threshold": {"post_ppp": 1.0, "prroll_ppp": 1.1}
    },
    "shot_creator": {
        "display": "Shot Creator",
        "icon": "Sparkles",
        "color": "#8b5cf6",  # Violet
        "description": "Creates own shot effectively",
        "threshold": {"iso_ppp": 1.0, "iso_possessions": 2.0}
    },
    "two_way": {
        "display": "Two-Way",
        "icon": "Shield",
        "color": "#10b981",  # Emerald
        "description": "Impact on both ends",
        "threshold": {"def_rating": 110.0, "off_rating": 110.0}  # Below 110 is good for def
    },
    "closer": {
        "display": "Closer",
        "icon": "Clock",
        "color": "#ec4899",  # Pink
        "description": "Performs in clutch moments",
        "threshold": {"4q_pts": 6.0}  # 6+ pts in 4th quarter average
    }
}


class BDLPlayerBadgeService:
    """
    Service for generating player performance badges from BDL Advanced Stats.
    
    Replaces the legacy nba_api based service which was failing due to
    stats.nba.com bot protection (JSON decode errors).
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.badges_collection = db.bdl_player_badges
        self.master_hub = db[COLL("master_hub", "nba")]
        self._cache = {}
    
    async def _make_request(
        self, 
        endpoint: str, 
        params: Dict = None,
        retries: int = 3
    ) -> Optional[Dict]:
        """Make authenticated request to BDL API with retry logic."""
        url = f"{BDL_BASE_URL}{endpoint}"
        headers = {"Authorization": BDL_API_KEY}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(retries):
                try:
                    response = await client.get(url, headers=headers, params=params)
                    
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 429:
                        wait_time = int(response.headers.get("Retry-After", 5))
                        logger.warning(f"[BDL_BADGES] Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"[BDL_BADGES] {endpoint} returned {response.status_code}")
                        return None
                        
                except Exception as e:
                    logger.error(f"[BDL_BADGES] Request error (attempt {attempt+1}): {e}")
                    if attempt < retries - 1:
                        await asyncio.sleep(1)
                    continue
        
        return None
    
    async def fetch_advanced_stats(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch general/advanced stats: Usage%, PIE, True Shooting%, Ratings.
        
        Endpoint: /season_averages/general?type=advanced
        """
        # Build params with proper array syntax for httpx
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "advanced")
        ]
        
        # Add player_ids as array params
        for pid in player_ids[:25]:  # Batch limit
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/general", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "usage_pct": self._pct_to_float(stats.get("usg_pct")),
                        "ts_pct": self._pct_to_float(stats.get("ts_pct")),
                        "pie": self._pct_to_float(stats.get("pie")),
                        "off_rating": stats.get("off_rtg"),
                        "def_rating": stats.get("def_rtg"),
                        "net_rating": stats.get("net_rtg"),
                        "ast_pct": self._pct_to_float(stats.get("ast_pct")),
                        "ast_to_ratio": stats.get("ast_to"),
                        "pace": stats.get("pace"),
                        "games_played": stats.get("gp")
                    }
        
        return results
    
    def _pct_to_float(self, val) -> Optional[float]:
        """Convert BDL percentage (0.xxx) to display percentage (xx.x)."""
        if val is None:
            return None
        return round(val * 100, 1) if val < 1 else round(val, 1)
    
    async def fetch_tracking_drives(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch drive tracking stats.
        
        Endpoint: /season_averages/tracking?type=drives
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "drives")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/tracking", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "drives": stats.get("drives"),
                        "drive_pts": stats.get("drive_pts"),
                        "drive_fgm": stats.get("drive_fgm"),
                        "drive_fga": stats.get("drive_fga"),
                        "drive_fg_pct": self._pct_to_float(stats.get("drive_fg_pct"))
                    }
        
        return results
    
    async def fetch_tracking_passing(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch passing tracking stats.
        
        Endpoint: /season_averages/tracking?type=passing
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "passing")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/tracking", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "potential_ast": stats.get("potential_ast"),
                        "ast_pts_created": stats.get("ast_pts_created"),
                        "secondary_ast": stats.get("secondary_ast"),
                        "passes_made": stats.get("passes_made")
                    }
        
        return results
    
    async def fetch_tracking_catchshoot(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch catch & shoot tracking stats.
        
        Endpoint: /season_averages/tracking?type=catchshoot
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "catchshoot")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/tracking", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "catch_shoot_fgm": stats.get("catch_shoot_fgm"),
                        "catch_shoot_fga": stats.get("catch_shoot_fga"),
                        "catch_shoot_fg_pct": self._pct_to_float(stats.get("catch_shoot_fg_pct")),
                        "catch_shoot_fg3m": stats.get("catch_shoot_fg3m"),
                        "catch_shoot_fg3a": stats.get("catch_shoot_fg3a"),
                        "catch_shoot_fg3_pct": self._pct_to_float(stats.get("catch_shoot_fg3_pct")),
                        "catch_shoot_pts": stats.get("catch_shoot_pts")
                    }
        
        return results
    
    async def fetch_hustle_stats(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch hustle stats: deflections, contested shots, loose balls, charges.
        
        Endpoint: /season_averages/hustle (no type required)
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/hustle", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "deflections": stats.get("deflections"),
                        "contested_shots": stats.get("contested_shots"),
                        "contested_shots_2pt": stats.get("contested_shots_2pt"),
                        "contested_shots_3pt": stats.get("contested_shots_3pt"),
                        "loose_balls_recovered": stats.get("loose_balls_recovered"),
                        "charges_drawn": stats.get("charges_drawn"),
                        "screen_assists": stats.get("screen_assists")
                    }
        
        return results
    
    async def fetch_playtype_prhandler(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch P&R Ball Handler playtype stats.
        
        Endpoint: /season_averages/playtype?type=prballhandler
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "prballhandler")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/playtype", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "prhandler_ppp": stats.get("ppp"),
                        "prhandler_possessions": stats.get("possessions"),
                        "prhandler_pts": stats.get("pts"),
                        "prhandler_fg_pct": self._pct_to_float(stats.get("fg_pct"))
                    }
        
        return results
    
    async def fetch_playtype_isolation(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch isolation playtype stats.
        
        Endpoint: /season_averages/playtype?type=isolation
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "isolation")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/playtype", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "iso_ppp": stats.get("ppp"),
                        "iso_possessions": stats.get("possessions"),
                        "iso_pts": stats.get("pts"),
                        "iso_fg_pct": self._pct_to_float(stats.get("fg_pct"))
                    }
        
        return results
    
    async def fetch_playtype_postup(self, player_ids: List[int]) -> Dict[int, Dict]:
        """
        Fetch post-up playtype stats.
        
        Endpoint: /season_averages/playtype?type=postup
        """
        params = [
            ("season", CURRENT_SEASON),
            ("season_type", "regular"),
            ("type", "postup")
        ]
        
        for pid in player_ids[:25]:
            params.append(("player_ids[]", pid))
        
        data = await self._make_request("/season_averages/playtype", params)
        
        results = {}
        if data and "data" in data:
            for item in data["data"]:
                player_info = item.get("player", {})
                pid = player_info.get("id")
                stats = item.get("stats", {})
                if pid:
                    results[pid] = {
                        "post_ppp": stats.get("ppp"),
                        "post_possessions": stats.get("possessions"),
                        "post_pts": stats.get("pts"),
                        "post_fg_pct": self._pct_to_float(stats.get("fg_pct"))
                    }
        
        return results
    
    async def fetch_all_stats_for_player(self, bdl_id: int) -> Dict[str, Any]:
        """
        Fetch all advanced stats categories for a single player.
        
        Returns merged dict of all stat types.
        """
        player_ids = [bdl_id]
        
        # Fetch all categories in parallel
        results = await asyncio.gather(
            self.fetch_advanced_stats(player_ids),
            self.fetch_tracking_drives(player_ids),
            self.fetch_tracking_passing(player_ids),
            self.fetch_tracking_catchshoot(player_ids),
            self.fetch_hustle_stats(player_ids),
            self.fetch_playtype_prhandler(player_ids),
            self.fetch_playtype_isolation(player_ids),
            self.fetch_playtype_postup(player_ids),
            return_exceptions=True
        )
        
        # Merge all results
        merged = {}
        for result in results:
            if isinstance(result, dict) and bdl_id in result:
                merged.update(result[bdl_id])
        
        return merged
    
    def resolve_badges(self, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Resolve which badges a player qualifies for based on stats.
        
        Returns list of badge objects with display info.
        """
        badges = []
        
        # Volume Scorer - High usage
        usage = stats.get("usage_pct")
        if usage and usage >= 25.0:
            badges.append({
                "badge_key": "volume_scorer",
                **BADGE_DEFINITIONS["volume_scorer"],
                "stat_value": round(usage, 1),
                "stat_label": f"{round(usage, 1)}% USG"
            })
        
        # Efficient - High TS% or PIE
        ts = stats.get("ts_pct")
        pie = stats.get("pie")
        if ts and ts >= 60.0:
            badges.append({
                "badge_key": "efficient",
                **BADGE_DEFINITIONS["efficient"],
                "stat_value": round(ts, 1),
                "stat_label": f"{round(ts, 1)}% TS"
            })
        elif pie and pie >= 15.0:
            badges.append({
                "badge_key": "efficient",
                **BADGE_DEFINITIONS["efficient"],
                "stat_value": round(pie, 1),
                "stat_label": f"{round(pie, 1)} PIE"
            })
        
        # Playmaker - High AST% or potential assists
        ast_pct = stats.get("ast_pct")
        potential_ast = stats.get("potential_ast")
        if ast_pct and ast_pct >= 25.0:
            badges.append({
                "badge_key": "playmaker",
                **BADGE_DEFINITIONS["playmaker"],
                "stat_value": round(ast_pct, 1),
                "stat_label": f"{round(ast_pct, 1)}% AST"
            })
        elif potential_ast and potential_ast >= 8.0:
            badges.append({
                "badge_key": "playmaker",
                **BADGE_DEFINITIONS["playmaker"],
                "stat_value": round(potential_ast, 1),
                "stat_label": f"{round(potential_ast, 1)} pot. AST"
            })
        
        # Floor General - Elite AST/TO ratio
        ast_to = stats.get("ast_to_ratio")
        if ast_to and ast_to >= 2.5:
            badges.append({
                "badge_key": "floor_general",
                **BADGE_DEFINITIONS["floor_general"],
                "stat_value": round(ast_to, 2),
                "stat_label": f"{round(ast_to, 2)} AST/TO"
            })
        
        # 3PT Assassin - High catch & shoot efficiency
        cs_3pct = stats.get("catch_shoot_fg3_pct")
        cs_3pa = stats.get("catch_shoot_fg3a")
        if cs_3pct and cs_3pa and cs_3pct >= 40.0 and cs_3pa >= 3.0:
            badges.append({
                "badge_key": "3pt_assassin",
                **BADGE_DEFINITIONS["3pt_assassin"],
                "stat_value": round(cs_3pct, 1),
                "stat_label": f"{round(cs_3pct, 1)}% C&S 3PT"
            })
        
        # Motor - High hustle stats
        deflections = stats.get("deflections")
        contested = stats.get("contested_shots")
        if deflections and deflections >= 2.0:
            badges.append({
                "badge_key": "motor",
                **BADGE_DEFINITIONS["motor"],
                "stat_value": round(deflections, 1),
                "stat_label": f"{round(deflections, 1)} DEF/G"
            })
        elif contested and contested >= 8.0:
            badges.append({
                "badge_key": "motor",
                **BADGE_DEFINITIONS["motor"],
                "stat_value": round(contested, 1),
                "stat_label": f"{round(contested, 1)} CONT/G"
            })
        
        # Paint Beast - High post-up or P&R roll efficiency
        post_ppp = stats.get("post_ppp")
        post_poss = stats.get("post_possessions")
        prroll_ppp = stats.get("prhandler_ppp")
        
        if post_ppp and post_poss and post_ppp >= 1.0 and post_poss >= 2.0:
            badges.append({
                "badge_key": "paint_beast",
                **BADGE_DEFINITIONS["paint_beast"],
                "stat_value": round(post_ppp, 2),
                "stat_label": f"{round(post_ppp, 2)} PPP Post"
            })
        elif prroll_ppp and prroll_ppp >= 1.1:
            badges.append({
                "badge_key": "paint_beast",
                **BADGE_DEFINITIONS["paint_beast"],
                "stat_value": round(prroll_ppp, 2),
                "stat_label": f"{round(prroll_ppp, 2)} PPP P&R"
            })
        
        # Shot Creator - High isolation efficiency
        iso_ppp = stats.get("iso_ppp")
        iso_poss = stats.get("iso_possessions")
        if iso_ppp and iso_poss and iso_ppp >= 1.0 and iso_poss >= 2.0:
            badges.append({
                "badge_key": "shot_creator",
                **BADGE_DEFINITIONS["shot_creator"],
                "stat_value": round(iso_ppp, 2),
                "stat_label": f"{round(iso_ppp, 2)} PPP ISO"
            })
        
        # Two-Way - Good on both ends
        def_rating = stats.get("def_rating")
        off_rating = stats.get("off_rating")
        if def_rating and off_rating and def_rating <= 110.0 and off_rating >= 110.0:
            net = off_rating - def_rating
            badges.append({
                "badge_key": "two_way",
                **BADGE_DEFINITIONS["two_way"],
                "stat_value": round(net, 1),
                "stat_label": f"+{round(net, 1)} NET"
            })
        
        return badges
    
    async def get_player_badges(
        self, 
        player_name: str = None, 
        bdl_id: int = None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Get badges for a player.
        
        Args:
            player_name: Player name to lookup
            bdl_id: BDL player ID (if known)
            force_refresh: Bypass cache
            
        Returns:
            Dict with player info, stats, and badges
        """
        # Lookup bdl_id if not provided
        if not bdl_id and player_name:
            player_doc = await self.master_hub.find_one(
                {"display_name": {"$regex": f"^{player_name}$", "$options": "i"}},
                {"bdl_id": 1, "display_name": 1, "team": 1}
            )
            if player_doc and player_doc.get("bdl_id"):
                bdl_id = player_doc["bdl_id"]
                player_name = player_doc.get("display_name", player_name)
        
        if not bdl_id:
            return {
                "error": f"Player not found: {player_name}",
                "badges": [],
                "stats": {}
            }
        
        # Check cache
        if not force_refresh:
            cached = await self.badges_collection.find_one(
                {"bdl_id": bdl_id},
                {"_id": 0}
            )
            
            if cached:
                fetched_at = cached.get("fetched_at")
                if fetched_at:
                    try:
                        fetch_time = datetime.fromisoformat(fetched_at)
                        age_hours = (datetime.now(timezone.utc) - fetch_time).total_seconds() / 3600
                        if age_hours < BADGE_CACHE_HOURS:
                            logger.debug(f"[BDL_BADGES] Using cached badges for {player_name}")
                            return cached
                    except Exception:
                        pass
        
        # Fetch fresh stats
        logger.info(f"[BDL_BADGES] Fetching badges for {player_name} (BDL ID: {bdl_id})")
        stats = await self.fetch_all_stats_for_player(bdl_id)
        
        if not stats:
            return {
                "player_name": player_name,
                "bdl_id": bdl_id,
                "error": "Failed to fetch stats from BDL",
                "badges": [],
                "stats": {}
            }
        
        # Resolve badges
        badges = self.resolve_badges(stats)
        
        # Build result document
        result = {
            "player_name": player_name,
            "bdl_id": bdl_id,
            "badges": badges,
            "badge_count": len(badges),
            "stats": stats,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "bdl_advanced"
        }
        
        # Cache result
        await self.badges_collection.update_one(
            {"bdl_id": bdl_id},
            {"$set": result},
            upsert=True
        )
        
        logger.info(f"[BDL_BADGES] Generated {len(badges)} badges for {player_name}")
        
        return result
    
    async def sync_badges_for_players(
        self, 
        player_names: List[str] = None,
        bdl_ids: List[int] = None
    ) -> Dict[str, Any]:
        """
        Batch sync badges for multiple players.
        
        Args:
            player_names: List of player names
            bdl_ids: List of BDL IDs
            
        Returns:
            Sync results summary
        """
        # Get bdl_ids from names if needed
        if not bdl_ids and player_names:
            bdl_ids = []
            for name in player_names:
                player_doc = await self.master_hub.find_one(
                    {"display_name": {"$regex": f"^{name}$", "$options": "i"}},
                    {"bdl_id": 1}
                )
                if player_doc and player_doc.get("bdl_id"):
                    bdl_ids.append(player_doc["bdl_id"])
        
        if not bdl_ids:
            # Get all players from master hub
            players = await self.master_hub.find(
                {"bdl_id": {"$exists": True}},
                {"bdl_id": 1, "display_name": 1}
            ).to_list(500)
            bdl_ids = [p["bdl_id"] for p in players if p.get("bdl_id")]
        
        logger.info(f"[BDL_BADGES] Syncing badges for {len(bdl_ids)} players...")
        
        results = {
            "synced": 0,
            "failed": 0,
            "total_badges": 0,
            "players": []
        }
        
        # Process in batches of 25 (BDL limit)
        batch_size = 25
        for i in range(0, len(bdl_ids), batch_size):
            batch = bdl_ids[i:i+batch_size]
            
            # Fetch all stat categories for batch
            advanced = await self.fetch_advanced_stats(batch)
            drives = await self.fetch_tracking_drives(batch)
            passing = await self.fetch_tracking_passing(batch)
            catchshoot = await self.fetch_tracking_catchshoot(batch)
            hustle = await self.fetch_hustle_stats(batch)
            prhandler = await self.fetch_playtype_prhandler(batch)
            isolation = await self.fetch_playtype_isolation(batch)
            postup = await self.fetch_playtype_postup(batch)
            
            # Process each player in batch
            for pid in batch:
                try:
                    # Merge stats from all categories
                    stats = {}
                    for stat_dict in [advanced, drives, passing, catchshoot, hustle, prhandler, isolation, postup]:
                        if isinstance(stat_dict, dict) and pid in stat_dict:
                            stats.update(stat_dict[pid])
                    
                    if not stats:
                        results["failed"] += 1
                        continue
                    
                    # Get player name
                    player_doc = await self.master_hub.find_one(
                        {"bdl_id": pid},
                        {"display_name": 1}
                    )
                    player_name = player_doc.get("display_name", f"Player {pid}") if player_doc else f"Player {pid}"
                    
                    # Resolve badges
                    badges = self.resolve_badges(stats)
                    
                    # Store result
                    badge_doc = {
                        "player_name": player_name,
                        "bdl_id": pid,
                        "badges": badges,
                        "badge_count": len(badges),
                        "stats": stats,
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "source": "bdl_advanced"
                    }
                    
                    await self.badges_collection.update_one(
                        {"bdl_id": pid},
                        {"$set": badge_doc},
                        upsert=True
                    )
                    
                    results["synced"] += 1
                    results["total_badges"] += len(badges)
                    
                    if badges:
                        results["players"].append({
                            "name": player_name,
                            "badges": [b["badge_key"] for b in badges]
                        })
                        
                except Exception as e:
                    logger.error(f"[BDL_BADGES] Error processing player {pid}: {e}")
                    results["failed"] += 1
            
            # Rate limit between batches
            await asyncio.sleep(1)
        
        logger.info(f"[BDL_BADGES] Sync complete: {results['synced']} synced, {results['total_badges']} badges generated")
        
        return results


# Singleton instance
_badge_service: Optional[BDLPlayerBadgeService] = None


def get_bdl_badge_service(db: AsyncIOMotorDatabase) -> BDLPlayerBadgeService:
    """Get or create badge service instance."""
    global _badge_service
    if _badge_service is None or _badge_service.db != db:
        _badge_service = BDLPlayerBadgeService(db)
    return _badge_service
