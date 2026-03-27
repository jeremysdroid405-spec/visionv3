"""
Data Integrity Service
======================
Extracted from services.engines.demon_goblin_engine.py for modularity.

Handles data verification and integrity checking:
- Verification failure logging
- Data integrity status reporting
- NAJI Safeguard (roster matching)
"""
from typing import Dict, Any
from datetime import datetime, timezone
import logging

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class DataIntegrityService:
    """
    Service for data integrity verification (V3.1 Truth Engine).
    
    Features:
    - Log verification failures for audit
    - Report data integrity status
    - NAJI Safeguard: Verify player ID matches roster
    """
    
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.cached_board = db.dg_cached_board
        self.master_roster = db.dg_master_roster
        self.sync_log = db.dg_sync_log
        self.verification_failures = db.dg_verification_failures
    
    async def log_verification_failure(
        self,
        player_name: str,
        failure_type: str,
        details: Dict[str, Any],
        sync_date: str
    ) -> None:
        """
        Log verification failures to MongoDB for audit and data status reporting.
        V3.1 Truth Engine: All failures are logged for the data status endpoint.
        """
        try:
            failure_doc = {
                "player_name": player_name,
                "failure_type": failure_type,
                "details": details,
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "sync_date": sync_date
            }
            
            await self.verification_failures.insert_one(failure_doc)
            logger.info(f"[VERIFICATION LOG] Logged {failure_type} failure for {player_name}")
        except Exception as e:
            logger.error(f"[VERIFICATION LOG] Failed to log failure: {e}")
    
    async def get_data_integrity_status(self, current_date: str) -> Dict[str, Any]:
        """
        V3.1 Truth Engine: Report data integrity status for the latest sync.
        
        Used by /api/v3/data-status endpoint for the frontend status light.
        
        Returns:
            - status: "verified" | "discrepancy_found" | "no_data"
            - verified_count: Number of props that passed verification
            - failed_count: Number of props that failed verification
            - failure_details: Recent failures with types
            - last_sync: Timestamp of last sync
        """
        try:
            # Count verified vs failed props in cached_board
            total_props = 0
            verified_props = 0
            failed_props = 0
            unverified_props = 0
            
            # Get all players from cached board
            players = await self.cached_board.find({}).to_list(None)
            
            for player in players:
                for prop in player.get("props", []):
                    total_props += 1
                    if prop.get("source_verified"):
                        verified_props += 1
                    elif prop.get("verification_status") in ["HALLUCINATION_DETECTED", "DISCREPANCY", "NAJI_SAFEGUARD_FAILED"]:
                        failed_props += 1
                    else:
                        unverified_props += 1
            
            # Get recent verification failures
            recent_failures = await self.verification_failures.find(
                {"sync_date": current_date},
                {"_id": 0}
            ).sort("logged_at", -1).limit(10).to_list(None)
            
            # Count failure types
            failure_types = {}
            for failure in recent_failures:
                ftype = failure.get("failure_type", "unknown")
                failure_types[ftype] = failure_types.get(ftype, 0) + 1
            
            # Get last sync time
            sync_log = await self.sync_log.find_one(
                {"type": "cached_board"},
                {"_id": 0, "synced_at": 1}
            )
            last_sync = sync_log.get("synced_at") if sync_log else None
            
            # Determine overall status
            if total_props == 0:
                status = "no_data"
            elif failed_props > 0:
                status = "discrepancy_found"
            elif verified_props > 0:
                status = "verified"
            else:
                status = "pending_verification"
            
            return {
                "success": True,
                "status": status,
                "sync_date": current_date,
                "last_sync": last_sync,
                "total_props": total_props,
                "verified_count": verified_props,
                "failed_count": failed_props,
                "unverified_count": unverified_props,
                "verification_rate": round((verified_props / total_props * 100), 2) if total_props > 0 else 0,
                "failure_types": failure_types,
                "recent_failures": recent_failures[:5],
                "naji_safeguard_enabled": True
            }
            
        except Exception as e:
            logger.error(f"[DATA STATUS] Error getting integrity status: {e}")
            return {
                "success": False,
                "status": "error",
                "error": str(e)
            }
    
    async def verify_player_roster_match(
        self,
        player_name: str,
        player_id: int,
        team_abbrev: str
    ) -> bool:
        """
        NAJI SAFEGUARD: Verify player ID matches active roster for today.
        If name matches but playerID doesn't match today's roster, KILL the data.
        """
        try:
            # Check if player is in master roster with matching ID
            roster_player = await self.master_roster.find_one({
                "player_name": {"$regex": f"^{player_name}$", "$options": "i"},
                "team": team_abbrev
            })
            
            if not roster_player:
                logger.warning(f"[NAJI SAFEGUARD] {player_name} not found in master roster")
                return False
            
            # If we have a BDL ID stored, verify it matches
            stored_id = roster_player.get("bdl_player_id")
            if stored_id and stored_id != player_id:
                logger.error(
                    f"[NAJI SAFEGUARD] ID MISMATCH: {player_name} - "
                    f"Roster ID: {stored_id}, Provided ID: {player_id} - DATA KILLED"
                )
                return False
            
            return True
        except Exception as e:
            logger.error(f"[NAJI SAFEGUARD] Verification error for {player_name}: {e}")
            return False
