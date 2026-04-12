"""
Live Injury Micro-Sync Service
==============================
High-frequency injury polling system decoupled from main data sync.

Features:
- 60-second polling interval (independent of main sync)
- Lightweight API calls (injury data only)
- Dedicated `live_injuries` collection with 60-second max cache
- JIT (Just-In-Time) delta checks before tier finalization

This service addresses latency issues where injury updates lag behind
breaking news networks.
"""
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
import aiohttp
import os

logger = logging.getLogger(__name__)

# Collection name for live injuries (separate from main injuries)
LIVE_INJURIES_COLLECTION = "live_injuries"

# Cache TTL in seconds
CACHE_TTL_SECONDS = 60

# Polling interval
POLLING_INTERVAL_SECONDS = 60


class LiveInjuryMicroSync:
    """
    High-frequency injury sync service.
    
    Runs independently of main sync engine with 60-second polling.
    Writes to dedicated live_injuries collection for JIT checks.
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.live_injuries = db[LIVE_INJURIES_COLLECTION]
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_sync: Optional[datetime] = None
        
    async def start_micro_loop(self):
        """Start the micro-sync polling loop."""
        if self._running:
            logger.warning("[INJURY-MICRO] Already running")
            return
            
        self._running = True
        self._task = asyncio.create_task(self._polling_loop())
        logger.info(f"[INJURY-MICRO] Started with {POLLING_INTERVAL_SECONDS}s interval")
        
    async def stop_micro_loop(self):
        """Stop the micro-sync polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[INJURY-MICRO] Stopped")
        
    async def _polling_loop(self):
        """Main polling loop - runs every 60 seconds."""
        while self._running:
            try:
                await self.fetch_live_injuries()
            except Exception as e:
                logger.error(f"[INJURY-MICRO] Poll failed: {e}")
            
            await asyncio.sleep(POLLING_INTERVAL_SECONDS)
    
    async def fetch_live_injuries(self) -> Dict[str, Any]:
        """
        Fetch live injury data from lightweight API.
        
        This is a minimal payload call - only injury status, no full player stats.
        Writes directly to live_injuries collection.
        """
        logger.info("[INJURY-MICRO] Fetching live injuries...")
        
        injuries_fetched = {
            "nba": [],
            "mlb": []
        }
        
        try:
            # Fetch NBA injuries from BallDontLie or ESPN
            nba_injuries = await self._fetch_nba_injuries()
            injuries_fetched["nba"] = nba_injuries
            
            # Fetch MLB injuries
            mlb_injuries = await self._fetch_mlb_injuries()
            injuries_fetched["mlb"] = mlb_injuries
            
            # Write to live_injuries collection with timestamp
            now = datetime.now(timezone.utc)
            
            # Upsert each injury record
            for sport, injuries in injuries_fetched.items():
                for injury in injuries:
                    await self.live_injuries.update_one(
                        {
                            "player_name": injury["player_name"],
                            "sport": sport
                        },
                        {
                            "$set": {
                                **injury,
                                "sport": sport,
                                "updated_at": now,
                                "expires_at": now + timedelta(seconds=CACHE_TTL_SECONDS)
                            }
                        },
                        upsert=True
                    )
            
            # Clean up expired entries
            await self.live_injuries.delete_many({
                "expires_at": {"$lt": now}
            })
            
            self._last_sync = now
            
            total = len(injuries_fetched["nba"]) + len(injuries_fetched["mlb"])
            logger.info(f"[INJURY-MICRO] Synced {total} injuries (NBA: {len(injuries_fetched['nba'])}, MLB: {len(injuries_fetched['mlb'])})")
            
            return {
                "success": True,
                "synced_at": now.isoformat(),
                "counts": {
                    "nba": len(injuries_fetched["nba"]),
                    "mlb": len(injuries_fetched["mlb"])
                }
            }
            
        except Exception as e:
            logger.error(f"[INJURY-MICRO] Fetch failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _fetch_nba_injuries(self) -> List[Dict]:
        """
        Fetch NBA injuries from lightweight API.
        Returns minimal payload: player_name, status, team, injury_type
        """
        injuries = []
        
        try:
            # Check existing injuries in dg_injuries collection
            dg_injuries = self.db["dg_injuries"]
            cursor = dg_injuries.find(
                {"sport": {"$in": ["nba", None]}},  # NBA or unspecified
                {
                    "_id": 0,
                    "player_name": 1,
                    "status": 1,
                    "team": 1,
                    "injury_type": 1,
                    "description": 1,
                    "severity": 1
                }
            )
            
            async for doc in cursor:
                status = (doc.get("status") or "").upper()
                if status in ["OUT", "DTD", "GTD", "DOUBTFUL", "QUESTIONABLE", "DAY-TO-DAY"]:
                    injuries.append({
                        "player_name": doc.get("player_name"),
                        "status": status,
                        "team": doc.get("team"),
                        "injury_type": doc.get("injury_type", "Unknown"),
                        "description": doc.get("description", ""),
                        "severity": doc.get("severity", "medium"),
                        "is_out": status in ["OUT", "DOUBTFUL"]
                    })
            
            # Also check bdl_injuries for fresh data
            bdl_injuries = self.db.get_collection("bdl_injuries")
            if bdl_injuries:
                async for doc in bdl_injuries.find({}, {"_id": 0}).limit(100):
                    player_name = doc.get("player_name") or doc.get("name")
                    status = (doc.get("status") or "").upper()
                    if player_name and status in ["OUT", "DTD", "GTD", "DOUBTFUL", "QUESTIONABLE"]:
                        # Check if already in list
                        if not any(i["player_name"] == player_name for i in injuries):
                            injuries.append({
                                "player_name": player_name,
                                "status": status,
                                "team": doc.get("team"),
                                "injury_type": doc.get("injury_type", "Unknown"),
                                "description": doc.get("comment", ""),
                                "severity": "high" if status in ["OUT", "DOUBTFUL"] else "medium",
                                "is_out": status in ["OUT", "DOUBTFUL"]
                            })
                            
        except Exception as e:
            logger.warning(f"[INJURY-MICRO] NBA fetch error: {e}")
        
        return injuries
    
    async def _fetch_mlb_injuries(self) -> List[Dict]:
        """
        Fetch MLB injuries from BallDontLie API.
        Returns minimal payload: player_name, status, team, injury_type
        """
        injuries = []
        
        # Get BDL API key
        api_key = os.environ.get("BDL_API_KEY") or os.environ.get("BALLDONTLIE_API_KEY")
        
        if not api_key:
            logger.warning("[INJURY-MICRO] No BDL API key found for MLB injuries")
            return injuries
        
        BDL_MLB_INJURIES_URL = "https://api.balldontlie.io/mlb/v1/player_injuries"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    BDL_MLB_INJURIES_URL,
                    params={"per_page": 100},
                    headers={"Authorization": api_key},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        for inj in data.get("data", []):
                            player = inj.get("player", {})
                            team = player.get("team", {})
                            player_name = player.get("full_name", "Unknown")
                            
                            if not player_name or player_name == "Unknown":
                                continue
                            
                            # Map BDL status to normalized status
                            bdl_status = (inj.get("status") or "").upper()
                            # BDL uses: "10-Day-IL", "60-Day-IL", "15-Day-IL", "Day-To-Day", "Out"
                            if "IL" in bdl_status:
                                status = "IL"
                                is_out = True
                            elif bdl_status == "OUT":
                                status = "OUT"
                                is_out = True
                            elif "DAY-TO-DAY" in bdl_status or bdl_status == "DTD":
                                status = "DTD"
                                is_out = False
                            else:
                                status = bdl_status if bdl_status else "UNKNOWN"
                                is_out = False
                            
                            injuries.append({
                                "player_name": player_name,
                                "status": status,
                                "team": team.get("abbreviation", "???"),
                                "injury_type": inj.get("type", "Unknown"),
                                "description": inj.get("short_comment", "") or inj.get("long_comment", ""),
                                "severity": "high" if is_out else "medium",
                                "is_out": is_out,
                                "return_date": inj.get("return_date"),
                                "source": "BDL"
                            })
                        
                        logger.info(f"[INJURY-MICRO] BDL MLB: {len(injuries)} injuries fetched")
                    else:
                        logger.warning(f"[INJURY-MICRO] BDL MLB returned {resp.status}")
                        
        except Exception as e:
            logger.warning(f"[INJURY-MICRO] BDL MLB fetch error: {e}")
        
        return injuries
    
    async def jit_check_player(self, player_name: str, sport: str = "nba") -> Optional[Dict]:
        """
        Just-In-Time check for a player's injury status.
        
        Called right before a player is finalized into a tier.
        Returns injury info if player is injured, None if healthy.
        """
        injury = await self.live_injuries.find_one(
            {
                "player_name": {"$regex": f"^{player_name}$", "$options": "i"},
                "sport": sport
            },
            {"_id": 0}
        )
        
        if injury and injury.get("is_out"):
            return injury
        
        return None
    
    async def jit_filter_picks(self, picks: List[Dict], sport: str = "nba") -> List[Dict]:
        """
        JIT filter picks - remove or flag players with active injuries.
        
        Called right before tier finalization.
        Returns filtered picks with injured players removed or flagged.
        """
        if not picks:
            return picks
        
        filtered = []
        flagged_count = 0
        removed_count = 0
        
        for pick in picks:
            player_name = pick.get("player_name")
            if not player_name:
                filtered.append(pick)
                continue
            
            injury = await self.jit_check_player(player_name, sport)
            
            if injury:
                if injury.get("is_out"):
                    # Player is OUT - remove from tier entirely
                    removed_count += 1
                    logger.info(f"[JIT] Removed {player_name} - {injury.get('status')}")
                    continue
                else:
                    # Player is DTD/GTD - flag but keep
                    pick["injury_flag"] = True
                    pick["injury_status"] = injury.get("status")
                    pick["injury_note"] = f"⚠️ {injury.get('status')}: {injury.get('injury_type', 'Unknown')}"
                    # Reduce board score
                    if pick.get("board_score"):
                        pick["board_score"] = max(0, pick["board_score"] - 15)
                    flagged_count += 1
            
            filtered.append(pick)
        
        if removed_count or flagged_count:
            logger.info(f"[JIT] Filtered {len(picks)} picks: {removed_count} removed, {flagged_count} flagged")
        
        return filtered
    
    async def get_live_injuries(self, sport: str = None) -> Dict[str, Any]:
        """
        Get current live injuries from cache.
        
        Returns all injuries if sport is None, or filtered by sport.
        """
        query = {}
        if sport:
            query["sport"] = sport
        
        injuries = await self.live_injuries.find(
            query,
            {"_id": 0}
        ).to_list(length=500)
        
        # Group by severity
        high_risk = [i for i in injuries if i.get("is_out")]
        medium_risk = [i for i in injuries if not i.get("is_out")]
        
        return {
            "success": True,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "high_risk": high_risk,
            "medium_risk": medium_risk,
            "total": len(injuries)
        }


# Singleton instance
_live_injury_service: Optional[LiveInjuryMicroSync] = None


def get_live_injury_service() -> Optional[LiveInjuryMicroSync]:
    """Get the singleton live injury service instance."""
    return _live_injury_service


def init_live_injury_service(db: AsyncIOMotorDatabase) -> LiveInjuryMicroSync:
    """Initialize the live injury service singleton."""
    global _live_injury_service
    if _live_injury_service is None:
        _live_injury_service = LiveInjuryMicroSync(db)
    return _live_injury_service
