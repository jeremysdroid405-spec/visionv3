"""
Sync Repository - Sync Log and Status
======================================
Handles sync status and logging operations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from .base import BaseRepository
from services.config.collection_names import COLL
import logging

logger = logging.getLogger(__name__)


class SyncRepository:
    """Repository for managing sync operations"""
    
    def __init__(self, db):
        self.db = db
        self.sync_log = BaseRepository(db[COLL.shared("sync_log")])
    
    # ==================== SYNC STATUS ====================
    
    async def get_sync_status(self, sync_type: str) -> Optional[Dict]:
        """Get sync status for a specific type"""
        return await self.sync_log.find_one({"type": sync_type})
    
    async def get_all_sync_status(self) -> List[Dict]:
        """Get all sync statuses"""
        return await self.sync_log.find_many()
    
    async def update_sync_status(
        self, 
        sync_type: str, 
        status: str = "success",
        details: Dict = None
    ) -> bool:
        """Update sync status for a type"""
        update_data = {
            "type": sync_type,
            "status": status,
            "synced_at": datetime.now(timezone.utc),
            "details": details or {}
        }
        return await self.sync_log.update_one(
            {"type": sync_type},
            {"$set": update_data},
            upsert=True
        )
    
    async def log_sync_start(self, sync_type: str) -> bool:
        """Log start of sync operation"""
        return await self.update_sync_status(
            sync_type,
            status="in_progress",
            details={"started_at": datetime.now(timezone.utc).isoformat()}
        )
    
    async def log_sync_complete(
        self, 
        sync_type: str, 
        records_processed: int = 0,
        duration_seconds: float = 0
    ) -> bool:
        """Log completion of sync operation"""
        return await self.update_sync_status(
            sync_type,
            status="success",
            details={
                "records_processed": records_processed,
                "duration_seconds": round(duration_seconds, 2),
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
        )
    
    async def log_sync_error(self, sync_type: str, error: str) -> bool:
        """Log sync error"""
        return await self.update_sync_status(
            sync_type,
            status="error",
            details={
                "error": error,
                "failed_at": datetime.now(timezone.utc).isoformat()
            }
        )
    
    async def get_last_sync_time(self, sync_type: str) -> Optional[datetime]:
        """Get last successful sync time"""
        doc = await self.sync_log.find_one(
            {"type": sync_type, "status": "success"},
            {"synced_at": 1}
        )
        return doc.get("synced_at") if doc else None
    
    async def is_sync_stale(self, sync_type: str, max_age_seconds: int = 3600) -> bool:
        """Check if sync is stale (older than max_age)"""
        last_sync = await self.get_last_sync_time(sync_type)
        if not last_sync:
            return True
        
        age = (datetime.now(timezone.utc) - last_sync).total_seconds()
        return age > max_age_seconds
    
    async def get_sync_summary(self) -> Dict[str, Any]:
        """Get summary of all sync operations"""
        statuses = await self.get_all_sync_status()
        
        return {
            "total_syncs": len(statuses),
            "successful": len([s for s in statuses if s.get("status") == "success"]),
            "failed": len([s for s in statuses if s.get("status") == "error"]),
            "in_progress": len([s for s in statuses if s.get("status") == "in_progress"]),
            "last_activity": max(
                [s.get("synced_at") for s in statuses if s.get("synced_at")],
                default=None
            ),
            "types": {s.get("type"): s.get("status") for s in statuses}
        }
    
    async def clear_sync_log(self) -> int:
        """Clear all sync logs"""
        return await self.sync_log.delete_many()
